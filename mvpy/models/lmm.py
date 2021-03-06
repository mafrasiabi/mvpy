#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 11 20:03:34 2019

@author: lukepinkel
"""
import re
import patsy
import collections
import numpy as np
import scipy as sp
import pandas as pd
import scipy.stats
import scipy.sparse as sps

from ..utils import linalg_utils, data_utils

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
        dim_dict[y] = {'nvars':Jdict[y].shape[1], 'n_groups':Zdict[x].shape[1]}
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



class LMM(object):

    def __init__(self, formula, data, error_structure=None, acov=None):
        '''
        Linear Mixed Model

        Parameters
        ----------
        formula: str
            Formula with using the R style of denoting random effects, 
            e.g., y~x+(1+z|id)

        data: DataFrame
            Pandas DataFrame containing n_obs by n_features including the
            relavent terms to the model

        error_structure: str, default None
            Error structure defaults to iid, but a factor level may be provided
            via a string referencing a column name, which will then be used to
            constructthe error covariance.  Implemented for multivariate linear
            models, where it is repeated across the multiple dependent variables,
            and has the structure Cov(Error) =  I_{n} \\otimes R_{m\\times m}
        acov: dict, default None
            Similar to random_effects, dictionary with keys indicating factors
            except the values need to be matrices that specify the covariance
            among observational units (row covariance)

        '''
        fe_form, random_effects = parse_random_effects(formula)
        fe_form = re.sub("\+$", "", fe_form)
        random_effects = dict([(y, x) for x, y in random_effects])
        yvars, fixed_effects = re.split("[~]", fe_form)
        yvars = re.split(",", re.sub("\(|\)", "", yvars))
        yvar = [x.strip() for x in yvars]
        n_obs = data.shape[0]
        X = patsy.dmatrix(fixed_effects, data=data, return_type='dataframe')
        fixed_effects = X.columns
        Z = []
        re_struct = collections.OrderedDict()
        
        # Determine if model is multivariate
        if type(yvar) is list: 
            n_vars = len(yvar)
            yvnames = yvar
        else:
            n_vars = 1
            yvnames = [yvar]
         
        res_names = [] # might be better off renamed re_names; may be typo
        for key in random_effects.keys():
            # dummy encode the groupings and get random effect variate
            Ji = data_utils.dummy_encode(data[key], complete=True)
            Zij = patsy.dmatrix(random_effects[key], data=data,
                                return_type='dataframe')
            # stratify re variable by dummy columns
            Zi = linalg_utils.khatri_rao(Ji.T, Zij.T).T
            if n_vars>1:
                # This should be changed to kron(Zi, eye)
                Kl = linalg_utils.kmat(Zi.shape[0], n_vars)
                Kr = linalg_utils.kmat(n_vars, Zi.shape[1])
                Zi = Kl.dot(np.kron(np.eye(n_vars), Zi)).dot(Kr)
                
            Z.append(Zi)
            k = Zij.shape[1]*n_vars
            # RE dependence structure
            if (acov is not None):
                if acov[key] is not None: # dependence structure for each RE
                    acov_i = acov[key]
                else:                     # single dependence for all REs
                    acov_i = np.eye(Ji.shape[1])
            else:                         # IID
                acov_i = np.eye(Ji.shape[1])
            re_struct[key] = {'n_units': Ji.shape[1],
                              'n_level_effects': Zij.shape[1],
                              'cov_re_dims': k,
                              'n_params': ((k + 1.0) * k) / 2.0,
                              'vcov': np.eye(k),
                              'params': linalg_utils.vech(np.eye(k)),
                              'acov': acov_i}
            if len(yvnames)>1:
                names = [x+": "+y for x in yvnames for y in
                         Zij.columns.tolist()]
                names = np.array(names)
            else:  
                names = np.array(Zij.columns.tolist())
            names_a = names[np.triu_indices(k)[0]]
            names_b = names[np.triu_indices(k)[1]]
            for r in range(len(names_a)):
                res_names.append(key+'|'+names_a[r]+' x '+names_b[r])
        

        Z = np.concatenate(Z, axis=1)

        error_struct = collections.OrderedDict()
        error_struct['vcov'] = np.eye(n_vars)
        error_struct['acov'] = np.eye(n_obs)
        error_struct['params'] = linalg_utils.vech(np.eye(n_vars))
        if len(yvnames)>1&(type(yvnames) is list):
            tmp = []
            for i, x in enumerate(yvnames):
                for j, y in enumerate(yvnames):
                    if i <= j:
                        tmp.append(x+": "+y+" error_var")
            res_names += tmp
        else:  
            res_names += ['error_var']
            
        # Vectorize equations - Add flexibility for dependent variable specific
        # design matrices
        if n_vars==1:
            y = data[yvar]
        else:
            y = linalg_utils.vecc(data[yvar].values.T)
            X = np.kron(X, np.eye(n_vars))

        var_params = np.concatenate([re_struct[key]["params"]
                                     for key in re_struct.keys()])
        err_params = error_struct['params']
        partitions = [0]+[re_struct[key]['n_params']
                          for key in re_struct.keys()]
        partitions += [len(error_struct['params'])]
        theta = np.concatenate([var_params, err_params])
        partitions2 = [0]+[re_struct[key]['n_units']
                           * re_struct[key]['cov_re_dims']
                           for key in re_struct.keys()]
        partitions2 = np.cumsum(partitions2)
        var_struct = collections.OrderedDict()
        for key in re_struct.keys():
            var_struct[key] = [re_struct[key]['vcov'].shape,
                               re_struct[key]['acov']]
        var_struct['error'] = [error_struct['vcov'].shape,
                               error_struct['acov']]
        # Get Z and Z otimes Z for each RE
        Zs = collections.OrderedDict()
        ZoZ = collections.OrderedDict()
        for i in range(len(re_struct)):
            key = list(re_struct)[i]
            Zs[key] = sps.csc_matrix(Z[:, partitions2[i]:partitions2[i+1]])
            # This can be time consuming for sufficiently dense Zs
            ZoZ[key] = sps.csc_matrix(sps.kron(Zs[key], Zs[key]))

        deriv_mats = collections.OrderedDict()
        for key in var_struct.keys():
            Sv_shape, Av = var_struct[key]
            Av_shape = Av.shape
            Kv = linalg_utils.kronvec_mat(Av_shape, Sv_shape, sparse=True)
            Ip = sps.csc_matrix(sps.eye(np.product(Sv_shape)))
            vecAv = sps.csc_matrix(linalg_utils.vecc(Av))
            D = sps.csc_matrix(Kv.dot(sps.kron(vecAv, Ip)))
    
            if key != 'error':
                D = sps.csc_matrix(ZoZ[key].dot(D))
            tmp = sps.csc_matrix(linalg_utils.dmat(int(np.sqrt(D.shape[1]))))
            deriv_mats[key] = D.dot(tmp)
       #if n_vars==1:
       #    bounds = [(0, None) if x == 1 else (None, None) for x in theta]
       #else:
       #    bounds = [(0, None) if x == 1 else (None, None) for x in theta[:-len(mv.vech(np.eye(n_vars)))]]
       #    bounds+= [(1, 1) if  x==1 else (0, 0) for x in mv.vech(np.eye(n_vars))]
        bounds = [(0, None) if x == 1 else (None, None) for x in theta]
        self.var_struct = var_struct
        self.deriv_mats = deriv_mats
        self.bounds = bounds
        self.theta = theta
        self.partitions = np.cumsum(partitions)
        J = sps.hstack([deriv_mats[key] for key in deriv_mats])
        self.jac_mats = [J[:, i].reshape(Z.shape[0], Z.shape[0], order='F')
                         for i in range(J.shape[1])]

        if n_vars==1:
            fe_names = fixed_effects.tolist()
        else: 
            fe_names = [[x+':'+yv for x in fixed_effects.tolist()] for yv in yvar]
            fe_names = [x for y in fe_names for x in y]
        self.X = linalg_utils._check_np(X)
        self.Z = linalg_utils._check_np(Z)
        self.y = linalg_utils._check_np(y)
        self.error_struct = error_struct
        self.re_struct = re_struct
        self.ZoZ = ZoZ
        self.res_names = res_names + fe_names
        self.n_vars = n_vars
        self.XZY = np.block([X, Z, y])
        self.XZ = np.block([X, Z])
        self.A = np.block([[X, Z], [np.zeros((Z.shape[1], X.shape[1])),
                           np.eye(Z.shape[1])]])
        self._is_multivar = n_vars>1

    def params2mats(self, theta=None):
        '''
        Create variance matrices from parameter vector
        Parameters
        ------------
        theta: array
            Vector containing relavent model terms
        '''
        if theta is None:
            theta = self.theta
        partitions = self.partitions
        error_struct = self.error_struct
        re_struct = self.re_struct

        Glist, Ginvlist, SigA = [], [], []
        for i, key in enumerate(re_struct.keys()):
            a, b = int(partitions[i]), int(partitions[i+1])
            Vi = linalg_utils.invech(theta[a:b])
            Ai = re_struct[key]['acov']
            Glist.append(np.kron(Ai, Vi))
            Ginvlist.append(np.kron(Ai, np.linalg.pinv(Vi)))
            SigA.append(Vi)
        p1, p2 = int(partitions[-2]), int(partitions[-1])
        Verr = linalg_utils.invech(theta[p1:p2])
        R = np.kron(Verr, error_struct['acov'])
        Rinv = np.kron(error_struct['acov'], np.linalg.inv(Verr))
        G, Ginv = sp.linalg.block_diag(*Glist), sp.linalg.block_diag(*Ginvlist)

        SigE = Verr.copy()
        return G, Ginv, SigA, R, Rinv, SigE

    def mmec(self, Rinv, Ginv):
        '''
        Mixed Model Equation Coefficient(MMEC) matrix construction
        Parameters
        ------------
        Rinv: array
          Inverse error covariance
        Ginv:
          Inverse random effect covariance
        '''
        F = self.XZ
        C = F.T.dot(Rinv).dot(F)
        k = Ginv.shape[0]
        C[-k:, -k:] += Ginv
        return C

    def mme_aug(self, Rinv, Ginv, C=None):
        '''
        Augmented Mixed Model Equation Coefficient matrix construction
        Parameters
        ------------
        Rinv: array
          Inverse error covariance
        Ginv: array
          Inverse random effect covariance
        C: array
          MMEC coefficient matrix

        '''
        if C is None:
            C = self.mmec(Rinv, Ginv)
        XZ, y = self.XZ, self.y
        t = y.T.dot(Rinv)
        b = t.dot(XZ)
        yRy = linalg_utils._check_np(t).dot(y)
        M = np.block([[C, b.T], [b, yRy]])
        return M

    def loglike(self, theta):
        '''
        Minus two times the restricted log likelihood
        Parameters
        ---------
        theta: array
            vector of parameters
        '''
        theta = linalg_utils._check_1d(theta)
        G, Ginv, SigA, R, Rinv, SigE = self.params2mats(theta)
        re_struct, error_struct = self.re_struct, self.error_struct
        C = self.mmec(Rinv, Ginv)
        M = self.mme_aug(Rinv, Ginv, C=C)
        L = linalg_utils.chol(M)
        logdetC = 2*np.sum(np.log(np.diag(L)[:-1]))
        yPy = L[-1, -1]**2
        logdetG = 0.0
        # This needs to be fixed for the more general case
        for key, Vi in list(zip(re_struct.keys(), SigA)):
            logdetG += re_struct[key]['n_units']*np.linalg.slogdet(Vi)[1]
        logdetR = error_struct['acov'].shape[0]*np.linalg.slogdet(SigE)[1]
        LL = logdetR+logdetC + logdetG + yPy
        return LL

    def fit(self, optimizer_kwargs={}, optimizer_options=None,
            maxiter=100, verbose=2, hess_opt=False):
        if optimizer_options is None:
            optimizer_options = {'verbose': verbose, 'maxiter': maxiter}
        if hess_opt is False:
            res = sp.optimize.minimize(self.loglike, self.theta,
                                       bounds=self.bounds,
                                       options=optimizer_options,
                                       method='trust-constr',
                                       jac=self.gradient,
                                       **optimizer_kwargs)
        else:
            res = sp.optimize.minimize(self.loglike, self.theta,
                                       bounds=self.bounds,
                                       options=optimizer_options,
                                       method='trust-constr',
                                       jac=self.gradient,
                                       hess=self.hessian,
                                       **optimizer_kwargs)

        self.params = res.x
        G, Ginv, SigA, R, Rinv, SigE = self.params2mats(res.x)
        self.G, self.Ginv, self.R, self.Rinv = G, Ginv, R, Rinv
        self.SigA, self.SigE = SigA, SigE
        W = linalg_utils.woodbury_inversion(self.Z, C=G, A=R)
        X = self.X
        XtW = X.T.dot(W)
        self.optimizer = res
        self.hessian_est = self.hessian(self.params)
        self.hessian_inv = np.linalg.pinv(self.hessian_est)
        self.SE_theta = np.sqrt(np.diag(self.hessian_inv))
        self.grd = self.gradient(self.params)
        self.gnorm = np.linalg.norm(self.grd) / len(self.params)
        self.b = linalg_utils.einv(XtW.dot(X)).dot(XtW.dot(self.y))
        self.SE_b = np.sqrt(np.diag(linalg_utils.einv(XtW.dot(X))))
        self.r = self.y - self.X.dot(self.b)
        self.u = G.dot(self.Z.T.dot(W).dot(self.r))
        res = pd.DataFrame(np.concatenate([self.params[:, None], self.b]),
                           columns=['Parameter Estimate'])
        res['Standard Error'] = np.concatenate([self.SE_theta, self.SE_b])
        res['t value'] = res['Parameter Estimate'] / res['Standard Error']
        res['p value'] = sp.stats.t.sf(np.abs(res['t value']),
                                       X.shape[0]-len(self.params)) * 2.0
        res.index = self.res_names
        self.res = res
        n_obs, k_params = self.X.shape[0], len(self.params)
        
        self.ll = self.loglike(self.params)
        self.aic = self.ll + (2 * k_params)
        self.aicc = self.ll + 2*k_params*n_obs / (n_obs - k_params - 1)
        self.bic = self.ll + k_params*np.log(n_obs)
        self.caic = self.ll + k_params * np.log(n_obs+1)
        self.r2_fe = 1 - np.var(self.y - self.X.dot(self.b)) / np.var(self.y)
        self.r2_re = 1 - np.var(self.y - self.Z.dot(self.u)) / np.var(self.y)
        self.r2 = 1 - np.var(self.y - self.predict()) / np.var(self.y)
        self.sumstats = np.array([self.aic, self.aicc, self.bic, self.caic,
                                  self.r2_fe, self.r2_re, self.r2])
        self.sumstats = pd.DataFrame(self.sumstats, index=['AIC', 'AICC', 'BIC',
                                                           'CAIC', 
                                                           'FixedEffectsR2',
                                                           'RandomEffectsR2', 
                                                           'R2'])
        
    def predict(self, X=None, Z=None):
        '''
        Returns the predicted values using both fixed and random effect
        estimates
        '''
        if X is None:
            X = self.X
        if Z is None:
            Z = self.Z
        return X.dot(self.b)+Z.dot(self.u)

    def gradient(self, theta):
        '''
        The gradient of minus two times the restricted log likelihood.  This is
        equal to

        \\partial\\mathcal{L}=vec(Py)'\\partial V-(vec(Py)\\otimes
                              vec(Py))'\\partial V

        Parameters
        ----------
        theta: array
          Vector of parameters

        Returns
        --------
        g: array
          gradient vector (1d for compatibility with scipy minimize)

        '''
        theta = linalg_utils._check_1d(theta)
        G, Ginv, SigA, R, Rinv, SigE = self.params2mats(theta)
        deriv_mats = self.deriv_mats
        X, Z, y = self.X, self.Z, self.y
        W = linalg_utils.woodbury_inversion(Z, Cinv=Ginv, Ainv=Rinv) 
        XtW = X.T.dot(W)
        XtWX_inv = linalg_utils.einv(XtW.dot(X))
        P = W - XtW.T.dot(XtWX_inv).dot(XtW)
        dP = P.reshape(np.product(P.shape), 1, order='F')
        Py = P.dot(y)
        PyPy = np.kron(Py, Py)
        # PyPy = vec(_check_2d(Py).dot(_check_2d(Py).T))[:, None] effecient
        # only at large heterogenous n
        g = []
        for key in deriv_mats.keys():
            JF_Omega = deriv_mats[key]
            g_i = JF_Omega.T.dot(dP) - JF_Omega.T.dot(PyPy)
            g.append(g_i)
        g = np.concatenate(g)
        return linalg_utils._check_1d(g)

    def hessian(self, theta):
        '''
        The hessian of minus two times the restricted log likelihood.  This is
        equal to

        \\partial\\mathcal{L}=\partial V'(P\\otimes Pyy'P - P)\\partial V
        
        In scalar form this is
        
        H_{ij}=H_{ji}=2y'P(\\partial V_{i})P(\\partial V_{j})Py - 
                      \tr{P(\\partial V_{i})P(\\partial V_{j})}

        Parameters
        ----------
        theta: array
          Vector of parameters

        Returns
        --------
        H: array
          Hessian matrix

        '''
        theta = linalg_utils._check_1d(theta)
        G, Ginv, SigA, R, Rinv, SigE = self.params2mats(theta)
        jac_mats = self.jac_mats
        X, Z, y = self.X, self.Z, self.y
        W = linalg_utils.woodbury_inversion(Z, Cinv=Ginv, Ainv=Rinv)
        XtW = X.T.dot(W)
        XtWX_inv = linalg_utils.einv(XtW.dot(X))
        P = W - XtW.T.dot(XtWX_inv).dot(XtW)
        # P = W - np.linalg.multi_dot([XtW.T, XtWX_inv, XtW])
        Py = P.dot(y)
        H = []
        #TODO Shit, just pre compute PJi and iterate over instead of current redundant matmuls
        PJ, yPJ = [], []
        for i, J in enumerate(jac_mats):
            PJ.append((J.T.dot(P)).T)
            yPJ.append((J.T.dot(Py)).T)
        indices = np.triu_indices(len(jac_mats)) 
        for i, j in list(zip(*indices)):
            PJi, PJj = PJ[i], PJ[j]
            yPJi, JjPy = yPJ[i], yPJ[j].T
            Hij = -(PJi.dot(PJj)).diagonal().sum()\
                        + (2 * (yPJi.dot(P)).dot(JjPy))[0]
            H.append(Hij[0])
        H = linalg_utils.invech(np.array(H))
        return H
    


