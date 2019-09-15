#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
Created on Sat Sep 14 23:29:28 2019

@author: lukepinkel
"""
import pandas as pd
import numpy as np
from numpy import eye, kron
from mvpy.api import (vine_corr, multi_rand, center,
                            vech, jmat)
from scipy.linalg import block_diag
from scipy.optimize import minimize
from mvpy.api import LMM

def initialize_lmm(n_units=50, n_unit_obs=5, n_levels=2, n_level_effects=2):
    Sv = vine_corr(n_levels*n_level_effects, 2)
    Se = vine_corr(n_levels, 2)
    
    Wv = eye(n_units)
    We = eye(n_units*n_unit_obs)
    
    Vc = kron(Sv, Wv)
    Ve = kron(Se, We)
    
    Zi = np.concatenate([jmat(n_unit_obs), np.arange(n_unit_obs)[:, None]], axis=1)
    Z = block_diag(*[Zi for i in range(n_units*2)])
    beta = np.random.normal(size=(2, 1))
    X = np.concatenate([Zi for i in range(n_units*n_levels)])
    
    U = center(multi_rand(Vc))
    E = center(multi_rand(Ve, size=Ve.shape[1]*2))
    e = E[[0]].T
    u = U[[0]].T
    
    y = X.dot(beta)+Z.dot(u)+e
    x = np.concatenate([np.arange(n_unit_obs) for i in range(n_units)])
    
    data = np.concatenate([y.reshape(n_units*n_unit_obs, 2, order='F'), x[:, None]], axis=1)
    data = pd.DataFrame(data, columns=['y1', 'y2', 'x1'])
    data['id'] = np.concatenate([jmat(n_unit_obs)*i for i in range(n_units)])
    fixed_effects = "~x1+1"
    random_effects = {"id":"~x1+1"}
    yvar = ['y1', 'y2']
    return fixed_effects, random_effects, yvar, data, Sv, Se
    
    
RC1, RC2 = [], []


for i in range(200):
    fixed_effects, random_effects, yvar, data, Sv, Se = initialize_lmm()
    model = LMM(fixed_effects, random_effects,  yvar, data)
    true_params = np.concatenate([vech(Sv), vech(Se)])
        
            
    res = minimize(model.loglike, model.theta, bounds=model.bounds, 
                   options={'verbose':0, 'maxiter':100}, method='trust-constr')
    
    res2 = minimize(model.loglike, model.theta, bounds=model.bounds, 
                   options={'verbose':0, 'maxiter':1000}, method='trust-constr',
                   jac=model.gradient)
    
    rc1 = np.concatenate([res.x[:, None], true_params[:, None]], axis=1)
    rc2 =  np.concatenate([res2.x[:, None], true_params[:, None]], axis=1)
    RC1.append(rc1)
    RC2.append(rc2)
    print(i)


RC1_D, RC2_D = [x[:, 0] - x[:, 1] for x in RC1], [x[:, 0] - x[:, 1] for x in RC2]
RC1_D = np.concatenate([x[:, None] for x in RC1_D], axis=1).T
RC2_D = np.concatenate([x[:, None] for x in RC2_D], axis=1).T

df1, df2 = pd.DataFrame(RC1_D), pd.DataFrame(RC2_D)
df1 = pd.DataFrame(df1.stack())
df2 = pd.DataFrame(df2.stack())

df1['c'] = df1.index.get_level_values(1)
df2['c'] = df2.index.get_level_values(1)
df1['method'] = [1]*len(df1)
df2['method'] = [2]*len(df2)
df = pd.concat([df1, df2], axis=0)

    


import seaborn as sns
sns.violinplot(x='c', y=0, data=df1)
sns.violinplot(x='c', y=0, data=df2)
sns.set_style('darkgrid')

sns.violinplot(x='c', y=0, hue='method', data=df, cut=0)
g = sns.pointplot(x='c', y=0, hue='method', data=df, join=False,
              dodge=0.2, capsize=.1, estimator=np.median)
g.axhline(0)
sns.boxplot(x='c', y=0, hue='method', data=df)

means = df.groupby(['c', 'method']).agg(['mean', 'median', 'std', 'skew', 'size'])






