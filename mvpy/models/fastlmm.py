#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Apr 27 15:34:24 2020

@author: lukepinkel
"""


import re # analysis:ignore
import jax # analysis:ignore
import time# analysis:ignore
import patsy # analysis:ignore
import timeit# analysis:ignore
import numba # analysis:ignore
import numpy as np # analysis:ignore
import scipy as sp # analysis:ignore
import pandas as pd # analysis:ignore
import seaborn as sns # analysis:ignore
import mvpy.api as mv # analysis:ignore
import matplotlib as mpl # analysis:ignore
import statsmodels.api as sm  # analysis:ignore
import matplotlib.pyplot as plt # analysis:ignore
import jax.numpy as jnp  # analysis:ignore
from mvpy.utils import linalg_utils, data_utils  # analysis:ignore
import _psparse as psparse # analysis:ignore
import mvpy.models.lmm as mvlm # analysis:ignore
import scipy.sparse as sps # analysis:ignore


def dmat(n):
    p = int(n * (n + 1) / 2)
    m = int(n**2)
    r = int(0)
    a = int(0)
    
    d = np.zeros((m,), dtype=np.int)
    t = np.ones((m,), dtype=np.double)
    for i in range(n):
        d[r:r+i] = i - n + np.cumsum(n - np.arange(0, i)) + 1
        r = r + i
        d[r:r+n-i] = np.arange(a, a+n-i)+1
        r = r + n - i
        a = a + n - i 
    D = sp.sparse.csc_matrix((t, (np.arange(m), d-1)), shape=(m, p))
    return D



def kmat(p, q):
    p = int(p)
    q = int(q)
    pq = p * q
    
    template = np.arange(0, int(q)) * int(p)
    z = np.ones((pq, ), dtype=np.int)
    k = np.zeros((pq,), dtype=np.int)
    for i in range(p):
        k[i*q:(i+1)*q] = template + i
    K = sp.sparse.csc_matrix((z, (np.arange(pq), k)), shape=(pq, pq))
    return K

def lmat(n):
    p = int(n * (n + 1) / 2)
    template = np.arange(n)
    z = np.ones((p,), dtype=np.int)
    k = np.zeros((p,), dtype=np.int)
    a = int(0)
    for i in range(n):
        k[a:a+n-i] = template
        template = template[:-1] + n +1
        a = a + n - i
    L = sp.sparse.csc_matrix((z, (np.arange(p), k)), shape=(p, n**2))
    return L  


@numba.jit(nopython=True)
def _lmat(n):
    p = int(n * (n + 1) / 2)
    template = np.arange(n)
    z = np.ones((p,), dtype=numba.int64)
    k = np.zeros((p,), dtype=numba.int64)
    a = int(0)
    for i in range(n):
        k[a:a+n-i] = template
        template = template[:-1] + n +1
        a = a + n - i
    return (z, (np.arange(p), k)), (p, n**2)  

    
def lmat_nb(n):
    data, shape = _lmat(n)
    K = sp.sparse.csc_matrix(data, shape=shape)
    return K

@numba.jit(nopython=True)
def _kmat(p, q):
    p = int(p)
    q = int(q)
    pq = p * q
    
    template = np.arange(0, int(q)) * int(p)
    z = np.ones((pq, ), dtype=numba.int64)
    k = np.zeros((pq,), dtype=numba.int64)
    for i in range(p):
        k[i*q:(i+1)*q] = template + i
    return (z, (np.arange(pq), k)), (pq, pq)
    
def kmat_nb(p, q):
    data, shape = _kmat(p, q)
    K = sp.sparse.csc_matrix(data, shape=shape)
    return K

@numba.jit(nopython=True)
def _dmat(n):
    p = int(n * (n + 1) / 2)
    m = int(n**2)
    r = int(0)
    a = int(0)
    
    d = np.zeros((m,), dtype=numba.int64)
    t = np.ones((m,), dtype=np.double)
    for i in range(n):
        d[r:r+i] = i - n + np.cumsum(n - np.arange(0, i)) + 1
        r = r + i
        d[r:r+n-i] = np.arange(a, a+n-i)+1
        r = r + n - i
        a = a + n - i 
    
    return (t, (np.arange(m), d-1)), (m, p)
    
def dmat_nb(n):
    data, shape = _dmat(n)
    D = sp.sparse.csc_matrix(data, shape=shape)
    return D

def nmat_nb(n):
    K = kmat_nb(n, n)
    I = sp.sparse.eye(n**2)
    N = K + I
    return N

@numba.jit(nopython=True)
def khatri_rao(X, Y):
    n, p = X.shape
    m, q = Y.shape
    nm = n * m
    assert p == q
    Z = np.zeros((nm, p), dtype=np.double)

    for i in range(p):
        Z[:, i] = np.kron(np.asfortranarray(X[:, i]), np.asfortranarray(Y[:, i]))
    return Z
        
        

def sparse_cholesky(A, permute='MMD_AT_PLUS_A'):
    if sp.sparse.issparse(A) is False:
        A = sp.sparse.csc_matrix(A)
    lu = sp.sparse.linalg.splu(A, permc_spec=permute)
    n = A.shape[0]
    Pr = sp.sparse.dok_matrix((n, n))
    Pc = sp.sparse.dok_matrix((n, n))
    
    Pr[lu.perm_r.astype(int), np.arange(n)] = 1.0
    Pc[np.arange(n), lu.perm_c.astype(int)] = 1.0
    
    Pr = Pr.tocsc()
    Pc = Pc.tocsc()

    L, U = lu.L, lu.U
    L = Pr.T*L*sp.sparse.diags(U.diagonal()**0.5) * Pc.T
    return L
    
    
def kronvec_mat(A_dims, B_dims):
  n, p = A_dims
  q, r = B_dims

  Kv = sp.sparse.kron(sp.sparse.eye(p), kmat_nb(r, n))
  Kv = sp.sparse.kron(Kv, sp.sparse.eye(q))
  return Kv

def fastls(X, y):
    '''
    Fast albeit potentially numerically inaccurate algorithm to compute
    OLS coefficients, sum of square errors, and the covariance matrix for
    the coefficient estimates (given a correctly specified model)
    '''
    n, p = X.shape
    G = X.T.dot(X)
    c = X.T.dot(y)
    L = np.linalg.cholesky(G)
    w = np.linalg.solve(L, c)
    s2 =  (np.dot(y.T, y) - w.T.dot(w)) / (n - p)
    beta = np.linalg.solve(L.T, w)
    Linv = np.linalg.inv(L)
    Ginv = np.dot(Linv.T, Linv)
    beta_cov = s2 * Ginv
    return s2, beta, beta_cov
    


def add_chol_row(xnew, xold, L=None):
    xtx = xnew
    norm_xnew = np.sqrt(xtx)
    if L is None:
        L = np.atleast_2d(norm_xnew)
        return L
    else:
        Xtx = xold
        r = sp.linalg.solve(L, Xtx)
        rpp = np.sqrt(xtx - np.sum(r**2))
        A = np.block([[L, np.zeros((L.shape[0], 1))],
                       [r, np.atleast_1d(rpp)]])
        return A
    
@numba.jit(nopython=True)
def toeplitz_cholesky_lower_nb(n, A):
    g = np.zeros((2, n), dtype=np.double)
    for j in range(0, n):
        g[0, j] = A[j, 0]
    for j in range(1, n):
        g[1, j] = A[j, 0]
    L = np.zeros((n, n), dtype=np.double)
    for j in range(0, n):
        L[j, 0] = g[0, j]
    for j in range(n-1, 0, -1):
        g[0, j] = g[0, j-1]
    g[0, 0] = 0.0
    for i in range(1, n):
        rho = -g[1, i] / g[0, i]
        gamma = np.sqrt((1.0 - rho) * (1.0 + rho))
        for j in range(i, n):
            alpha = g[0, j]
            beta = g[1, j]
            g[0, j] = (alpha + rho * beta) / gamma
            g[1, j] = (rho * alpha + beta) / gamma
        for j in range(i, n):
            L[j, i] = g[0, j]
        for j in range(n-1, i, -1):
            g[0, j] = g[0, j-1]
        g[0, i] = 0.0
    return L

@numba.jit(nopython=True)
def vech(X):
    p = X.shape[0]
    tmp =  1 - np.tri(p, p, k=-1)
    tmp2 = tmp.flatten()
    ix = tmp2==1
    Y = X.T.flatten()[ix]
    return Y

@numba.jit(nopython=True)
def invech(v):
    '''
    Inverse half vectorization operator
    '''
    rows = int(np.round(.5 * (-1 + np.sqrt(1 + 8 * len(v)))))
    res = np.zeros((rows, rows))
    tmp =  1 - np.tri(rows, rows, k=-1)
    tmp2 = tmp.flatten()
    ix = tmp2==1
    Y = res.T.flatten()
    Y[ix] = v
    Y = Y.reshape(rows, rows)
    Y = Y + Y.T
    Y = Y - (np.eye(rows) * Y) / 2
    return Y


@jax.jit
def jax_vec(X):
    '''
    Takes an n \times p matrix and returns a 1 dimensional np vector
    '''
    return X.reshape(-1, order='F')

@jax.jit
def jax_invec(x, n_rows, n_cols):
    '''
    Takes an np 1 dimensional vector and returns an n \times p matrix
    '''
    return x.reshape(int(n_rows), int(n_cols), order='F')

@jax.jit
def jax_vech(X):
    '''
    Half vectorization operator; returns an \frac{(n+1)\times n}{2} vector of
    the stacked columns of unique items in a symmetric  n\times n matrix
    '''
    rix, cix = jnp.triu_indices(len(X))
    res = jnp.take(X.T, rix*len(X)+cix)
    return res

@jax.jit
def jax_invech(v):
    '''
    Inverse half vectorization operator
    '''
    rows = int(jnp.round(.5 * (-1 + jnp.sqrt(1 + 8 * len(v)))))
    res = jnp.zeros((rows, rows))
    res = jax.ops.index_update(res, jnp.triu_indices(rows), v)
    res = res + res.T - jnp.diag(jnp.diag(res))
    return res

@numba.jit(nopython=True)
def vine_corr(d, betaparams=10):
    P = np.zeros((d, d))
    S = np.eye(d)
    for k in range(d-1):
        for i in range(k+1, d):
            P[k, i] = np.random.beta(betaparams, betaparams)
            P[k, i] = (P[k, i] - 0.5)*2.0
            p = P[k, i]
            for l in range(k-1, 1, -1):
                p = p * np.sqrt((1 - P[l, i]**2)*(1 - P[l, k]**2)) + P[l, i]*P[l, k]
            S[k, i] = p
            S[i, k] = p
    u, V = np.linalg.eigh(S)
    umin = np.min(u[u>0])
    u[u<0] = [umin*0.5**(float(i+1)/len(u[u<0])) for i in range(len(u[u<0]))]
    S = V.dot(np.diag(u)).dot(V.T)
    v = np.diag(S)
    v = np.diag(1/np.sqrt(v))
    S = v.dot(S).dot(v)
    return S

@numba.jit(nopython=True)
def onion_corr(d, betaparams=10):
    beta = betaparams + (d - 2) / 2
    u = np.random.beta(beta, beta)
    r12 = 2 * u  - 1
    S = np.array([[1, r12], [r12, 1]])
    I = np.array([[1.0]])
    for i in range(3, d+1):
        beta -= 0.5
        r = np.sqrt(np.random.beta((i - 1) / 2, beta))
        theta = np.random.normal(0, 1, size=(i-1, 1))
        theta/= np.linalg.norm(theta)
        w = r * theta
        c, V = np.linalg.eig(S)
        R = (V * np.sqrt(c)).dot(V.T)
        q = R.dot(w)
        S = np.concatenate((np.concatenate((S, q), axis=1),
                            np.concatenate((q.T, I), axis=1)), axis=0)
    return S
        




def replace_duplicate_operators(match):
    return match.group()[-1:]

def parse_random_effects(formula):
    matches = re.findall("\([^)]+[|][^)]+\)", formula)
    groups = [re.search("\(([^)]+)\|([^)]+)\)", x).groups() for x in matches]
    frm = formula
    for x in matches:
        frm = frm.replace(x, "")
    fe_form = re.sub("(\+|\-)(\+|\-)+", replace_duplicate_operators, frm)
    return fe_form, groups

def construct_random_effects(groups, data, n_vars):
    re_vars, re_groupings = list(zip(*groups))
    re_vars, re_groupings = set(re_vars), set(re_groupings)
    Zdict = dict(zip(re_vars, [patsy.dmatrix(x, data=data, return_type='dataframe') for x in re_vars]))
    Jdict = dict(zip(re_groupings, [data_utils.dummy_encode(data[x], complete=True) for x in re_groupings]))
    dim_dict = {}
    Z = []
    for x, y in groups:
        dim_dict[y] = {'n_groups':Jdict[y].shape[1], 'n_vars':Zdict[x].shape[1]}
        Zi = linalg_utils.khatri_rao(Jdict[y].T, Zdict[x].T).T
        if n_vars>1:
            # This should be changed to kron(Zi, eye)
            Kl = linalg_utils.kmat(Zi.shape[0], n_vars)
            Kr = linalg_utils.kmat(n_vars, Zi.shape[1])
            Zi = Kl.dot(np.kron(np.eye(n_vars), Zi)).dot(Kr)
        Z.append(Zi)
    Z = np.concatenate(Z, axis=1)
    return Z, dim_dict

def construct_model_matrices(formula, data):
    fe_form, groups = parse_random_effects(formula)
    yvars, fe_form = re.split("[~]", fe_form)
    fe_form = re.sub("\+$", "", fe_form)
    yvars = re.split(",", re.sub("\(|\)", "", yvars))
    yvars = [x.strip() for x in yvars]
    n_vars = len(yvars)
    Z, dim_dict = construct_random_effects(groups, data, n_vars)
    X = patsy.dmatrix(fe_form, data=data, return_type='dataframe')
    if n_vars>1:
        y = linalg_utils.vecc(data[yvars].values.T)
        X = np.kron(X, np.eye(n_vars))
    else:
        y = data[yvars]

    return X, Z, y, dim_dict

def vech2vec(vh):
    A = invech(vh)
    v = A.reshape(-1, order='F')
    return v
    

def make_theta(dims):
    theta, indices, index_start = [], {}, 0
    for key, value in dims.items():
        n_vars = value['n_vars']
        n_params = int(n_vars * (n_vars+1) //2)
        indices[key] = np.arange(index_start, index_start+n_params)
        theta.append(vech(np.eye(n_vars)))
        index_start += n_params
    theta = np.concatenate(theta)
    return theta, indices

def create_gmats(theta, indices, dims, inverse=False):
    Gmats, g_indices, start = {}, {}, 0
    for key, value in dims.items():
        if key!='error':
            dims_i = dims[key]
            ng, nv = dims_i['n_groups'],  dims_i['n_vars']
            nv2, nvng = nv*nv, nv*ng
            theta_i = theta[indices[key]]
            if inverse:
                theta_i = np.linalg.inv(invech(theta_i)).reshape(-1, order='F')
            else:
                theta_i = vech2vec(theta_i)
            row = np.repeat(np.arange(nvng), nv)
            col = np.repeat(np.arange(ng)*nv, nv2)
            col = col + np.tile(np.arange(nv), nvng)
            data = np.tile(theta_i, ng)
            Gmats[key] = sps.csc_matrix((data, (row, col)))
            g_indices[key] = np.arange(start, start+len(data))
            start += len(data)
    return Gmats, g_indices
                
def update_gmat(theta, G, dims, indices, g_indices, inverse=False):
    for key in g_indices.keys():
        ng = dims[key]['n_groups']
        theta_i = theta[indices[key]]
        if inverse:
            theta_i = np.linalg.inv(invech(theta_i)).reshape(-1, order='F')
        else:
            theta_i = vech2vec(theta_i)
        G.data[g_indices[key]] = np.tile(theta_i, ng)
    return G
        
  
        
def sparse_woodbury_inversion(Umat, Vmat=None, C=None, Cinv=None, A=None, Ainv=None):
    if Ainv is None:
        Ainv = sps.linalg.inv(A)
    if Cinv is None:
        Cinv = sps.linalg.inv(C)
    if Vmat is None:
        Vmat = Umat.T
    T = Ainv.dot(Umat)
    H = sps.linalg.inv(Cinv + Vmat.dot(T))
    W = Ainv - T.dot(H).dot(Vmat).dot(Ainv)
    return W

def lndet_gmat(theta, dims, indices):
    lnd = 0.0
    for key, value in dims.items():
        if key!='error':
            dims_i = dims[key]
            ng = dims_i['n_groups']
            Sigma_i = invech(theta[indices[key]])
            lnd += ng*np.linalg.slogdet(Sigma_i)[1]
    return lnd
    
def lndet_cmat(M):  
    L = sparse_cholesky(M)
    LA = L.A
    logdetC = np.sum(2*np.log(np.diag(LA))[:-1])
    return logdetC
        
def get_derivmats(Zs, dims):
    start = 0
    deriv_mats = {}
    for key, value in dims.items():
        nv, ng =  value['n_vars'], value['n_groups']
        Sv_shape = nv, nv
        Av_shape = ng, ng
        Kv = linalg_utils.kronvec_mat(Av_shape, Sv_shape, sparse=True)
        Ip = sps.csc_matrix(sps.eye(np.product(Sv_shape)))
        vecAv = sps.csc_matrix(sps.eye(ng)).reshape((-1, 1), order='F')
        D = sps.csc_matrix(Kv.dot(sps.kron(vecAv, Ip)))
        if key != 'error':
                Zi = Zs[:, start:start+ng*nv]
                ZoZ = sps.kron(Zi, Zi)
                D = sps.csc_matrix(ZoZ.dot(D))
                start+=ng*nv
        tmp = sps.csc_matrix(linalg_utils.dmat(int(np.sqrt(D.shape[1]))))
        deriv_mats[key] = D.dot(tmp)
    return deriv_mats
       


class LME:
    
    def __init__(self, formula, data):
        X, Z, y, dims = construct_model_matrices(formula, data)
        dims['error'] = dict(n_groups=len(X), n_vars=1)

        theta, indices = make_theta(dims)
        XZ = sps.hstack([X, Z])
        C = XZ.T.dot(XZ)
        Xty = X.T.dot(y)
        Zty = Z.T.dot(y)
        b = np.vstack([Xty, Zty])
        Gmats, g_indices = create_gmats(theta, indices, dims)
        Gmats_inverse, _ = create_gmats(theta, indices, dims, inverse=True)
        G = sps.block_diag(list(Gmats.values())).tocsc()
        Ginv =  sps.block_diag(list(Gmats_inverse.values())).tocsc()
        Zs = sps.csc_matrix(Z)
        Ip = sps.eye(Zs.shape[0])
        self.G = G
        self.Ginv = Ginv
        self.g_indices = g_indices
        self.X = linalg_utils._check_2d(linalg_utils._check_np(X))
        self.Z = Z
        self.y = linalg_utils._check_2d(linalg_utils._check_np(y))
        self.XZ = XZ
        self.C = C
        self.Xty = Xty
        self.Zty = Zty
        self.b = b
        self.dims = dims
        self.indices = indices
        self.formula = formula
        self.data = data
        self.theta = theta
        self.Zs = Zs
        self.Ip = Ip
        self.deriv_mats = get_derivmats(Zs, dims)
        self.yty = y.T.dot(y)
    
    def _params_to_model(self, theta):
        G = update_gmat(theta, self.G.copy(), self.dims, self.indices, self.g_indices)
        Ginv = update_gmat(theta, self.G.copy(), self.dims, self.indices, self.g_indices, inverse=True)
        s = theta[-1]
        R = self.Ip * s
        Rinv = self.Ip / s 
        V = self.Zs.dot(G).dot(self.Zs.T) + R
        Vinv = sparse_woodbury_inversion(self.Zs, Cinv=Ginv, Ainv=Rinv.tocsc())
        W = psparse.pmultiply(Vinv, np.asfortranarray(self.X))
        return G, Ginv, R, Rinv, V, Vinv, W, s
    
    def loglike(self, theta):
        G, Ginv, R, Rinv, V, Vinv, W, s = self._params_to_model(theta)
        C = self.C.copy()/s
        k =  Ginv.shape[0]
        C[-k:, -k:] += Ginv
        logdetR = np.log(s) * self.Z.shape[0]
        logdetG = lndet_gmat(theta, self.dims, self.indices)
        yty = np.array(np.atleast_2d(self.yty/s))
        M = sps.bmat([[C, self.b/s],
                      [self.b.T/s, yty]])
        #L = sparse_cholesky(M)
        L = np.linalg.cholesky(M.A)
        ytPy = np.diag(L)[-1]**2
        logdetC = np.sum(2*np.log(np.diag(L))[:-1])
        ll = logdetC+logdetG+logdetR+ytPy
        return ll
    
    def gradient(self, theta):
        dims = self.dims
        G, Ginv, R, Rinv, V, Vinv, W, s = self._params_to_model(theta)
        XtW = W.T.dot(self.X)
        XtW_inv = np.linalg.inv(XtW)
        P = Vinv - np.linalg.multi_dot([W, XtW_inv, W.T])
        Py = P.dot(self.y)
        PyPy = np.kron(Py, Py)
        vecP = P.reshape((-1,1), order='F')
        grad = []
        for key in dims.keys():
            g = self.deriv_mats[key].T.dot(vecP)-self.deriv_mats[key].T.dot(PyPy)
            grad.append(g)
        grad = np.concatenate(grad)
        return grad
                
                
                        
        

    
    
    
"""
df = pd.DataFrame(np.kron(np.arange(200), np.ones(20)), columns=['id1'])
df['id2'] = np.kron(np.arange(50), np.ones(80))
df['id3'] = np.kron(np.arange(100), np.ones(40))

df['x1'] = np.random.normal(size=(4000))
df['x2'] = np.random.normal(size=(4000))
df['x3'] = np.random.normal(size=(4000))
df['x4'] = np.random.normal(size=(4000))
df['x5'] = np.random.normal(size=(4000))

df['y'] = 0

X, Z, y, dims = construct_model_matrices("y~x1+x5-1+(1+x2|id1)+(1|id2)+(1+x3+x4|id3)", data=df)
dims['error'] = dict(n_groups=len(X), n_vars=1)

G1 = np.array([[1.0, 0.2],
               [0.2, 1.0]])
G2 = np.array([[1.0]])
G3 = vine_corr(3)

U1 = sp.stats.multivariate_normal(np.zeros(2), G1).rvs(200).flatten()
U2 = sp.stats.multivariate_normal(np.zeros(1), G2).rvs(50)
U3 = sp.stats.multivariate_normal(np.zeros(3), G3).rvs(100).flatten()

beta = np.array([1.5, -1.0])
eta = X.dot(beta)+Z.dot(np.concatenate((U1, U2, U3)))
var = eta.var()
df['y'] = sp.stats.norm(eta, np.sqrt((1-0.5)/0.5*var)).rvs()

mod = LME("y~x1+x5-1+(1+x2|id1)+(1|id2)+(1+x3+x4|id3)", data=df)
lmm = mv.LMM("y~x1+x5-1+(1+x2|id1)+(1|id2)+(1+x3+x4|id3)", data=df)

theta = mod.theta
theta[-1] = 5

mod.loglike(theta)
mod.gradient(theta)
lmm.loglike(theta)
lmm.gradient(theta)

"""