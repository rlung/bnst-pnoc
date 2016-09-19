#%%

import numpy as np
from scipy import stats
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import seaborn as sns
import glob
import os
import re
from functools import partial
import statsmodels.formula.api as smf
from statsmodels.sandbox.regression.predstd import wls_prediction_std
from datetime import datetime
import pdb

# Import custom functions
import sys
dirs2try = ['C:/Users/randa/Dropbox (Stuber Lab)/Documents/Python/',
            'D:/Dropbox (Stuber Lab)/Documents/Python/',
            '/data/Dropbox (Stuber Lab)/Documents/Python/']

base_dir = []
for dir2try in dirs2try:
    if os.path.isdir(dir2try):
        sys.path.append(dir2try)
        base_dir = dir2try
        break
if base_dir:
    from my_functions import etho_extract, activity_map, interp_missing, bh_correction
else:
    print "my_function not imported. Directory not identified."
    
#%% Parameters

# Define if events or fluorescence data
event_data = False

sig_fps = 5
time_label = 'Recording_time'

# Time in open/close arm counted as transition
arm0_t_min = 5
center_t_limit = 5
arm1_t_min = 5

# Working directory
os.chdir(os.path.join(base_dir, 'PNOC'))

# Data directories
behav_files = glob.glob('PNOC_EPM_Behavior/*.xlsx')
if event_data:
    sig_dirs = glob.glob('PNOC Events_EPM/PNOC*')
else:
    sig_dirs = glob.glob('PNOC Traces_EPM/PNOC*')

# Directory to save data
now = datetime.now()
save_dir = 'data/data-' + now.strftime('%y%m%d-%H%M%S')
if not os.path.isdir(save_dir):
    os.makedirs(save_dir)


#%% Import data from all animals
# Relies on all data acquisition to be the same between animals, eg frame #.

# Import behavior
file_names = np.array([os.path.split(behav_file)[-1] for behav_file in behav_files])
file_num = len(file_names)

etho_out = map(etho_extract, behav_files)
behav_id_ord = [re.split('_|\.', file, 1)[0] for file in file_names]

# Import signals
sig_id_ord = [re.split('_|\.', os.path.split(sig_dir)[-1], 1)[0] for sig_dir in sig_dirs]
sigs_import = [np.load(os.path.join(sig_dir, 'extractedsignals.npy')) for sig_dir in sig_dirs]
sig_frame_num = np.min([x.shape[1] for x in sigs_import])


#%% Format data

data = [interp_missing(etho[0].T).T for etho in etho_out]
labels = [label.translate(None, '()<>/-').replace(' ', '_') for label in etho_out[0][1]]  # Assumes all labels are the same
time_ix = labels.index(time_label)
behav_frame_num = data[0].shape[0]
behav_fps = int(1 / np.diff(np.squeeze(data[0][:, time_ix])).mean())

# Normalize behavioral data
# Set range of values from -1 to 1. Exclude timestamps (first 2 columns).
data_norm = list(data)
for x in np.arange(file_num):
    var_maxes = np.max(data_norm[x][:, 2:], axis=0, keepdims=True)
    zero_max_mask = var_maxes == 0
    var_maxes[zero_max_mask] = 1
    data_norm[x][:, 2:] = data_norm[x][:, 2:] / np.repeat(var_maxes, behav_frame_num, axis=0)

# Truncate signal and events so all have the same number of frames
sigs_list = [sig[:, :sig_frame_num] for sig in sigs_import]

# Number of neurons per subject
num_cells_per_subj = [x.shape[0] for x in sigs_list]
cell_subj_id = [np.array(num_cells * [sig_id]) for num_cells, sig_id in zip(num_cells_per_subj, sig_id_ord)]

# Reshape array of signal into one matrix
sigs = np.concatenate(tuple(sigs_list), axis=0)
sigs_id = np.concatenate(tuple(cell_subj_id), axis=0)
sig_ts = np.arange(sig_frame_num, dtype=float) / sig_fps

# Normalize signal
if event_data:
    cell_std = np.std(sigs, axis=1, keepdims=True)
    sigs_z = sigs / cell_std.repeat(sig_frame_num,axis=1)
else:
    sigs_z = map(stats.zscore, sigs)
    sigs_z = np.column_stack(sigs_z).T  # WATCH OUT FOR THE TRANSPOSE!!!


#%% Create new behavioral data (for EPM)

data_norm2 = list(data_norm)  # copy list

for d in np.arange(len(data_norm2)):
    # Make it work for list of data...
    open_col = labels.index('In_zoneOpen_arms__centerpoint')
    close_col = labels.index('In_zoneClosed_arms__centerpoint')
    
    # Identify state transitions
    # Index corresponds to the first behavioral frame of transition.
    open_diff = np.diff(np.squeeze(data_norm2[d][:, open_col]))
    close_diff = np.diff(np.squeeze(data_norm2[d][:, close_col]))
    
    # Behavioral frame of change
    close_enter_mask = np.append(False, close_diff == 1)
    close_exit_mask = np.append(False, close_diff == -1)
    open_enter_mask = np.append(False, open_diff == 1)
    open_exit_mask = np.append(False, open_diff == -1)
    close_enter_frames = np.where(close_enter_mask)[0] + 1
    close_exit_frames = np.where(close_exit_mask)[0] + 1
    open_enter_frames = np.where(open_enter_mask)[0] + 1
    open_exit_frames = np.where(open_exit_mask)[0] + 1
    
    close_exit_num = len(close_exit_frames)
    open_enter_num = len(open_enter_frames)
    
    # Find close-to-open transitions
    close2open = np.zeros(behav_frame_num)
    last_frame = 0
    for frame in close_exit_frames:
    
        # Frame of close-arm entry
        # If no frame exists, must have started in closed arm, and first frame
        # should be used.
        close_enter_frames_before = np.where(frame - close_enter_frames > 0, close_enter_frames, 0)
        if np.any(close_enter_frames_before):
            close_enter_frame = np.max(close_enter_frames_before)
        else:
            close_enter_frame = 0
        
        # Frame of next closed-arm entry
        close_enter_frames_after = np.where(close_enter_frames - frame > 0,
                                            close_enter_frames, np.nan)
        if np.any(np.isfinite(close_enter_frames_after)):
            next_close_enter_frame = int(np.nanmin(close_enter_frames_after))
        else:
            next_close_enter_frame = behav_frame_num
        
        # Frame of next open-arm entry
        open_enter_frames_after = np.where(open_enter_frames - frame > 0,
                                           open_enter_frames, np.nan)              # first index in array of frame indices of open-arm entries after close-arm exit
        if np.any(np.isfinite(open_enter_frames_after)):
            next_open_enter_frame = int(np.nanmin(open_enter_frames_after))
        else:
            break
    
        # Frame of next open-arm exit
        open_exit_frames_after = np.where(open_exit_frames - frame > 0,
                                          open_exit_frames, np.nan)
        if np.any(np.isfinite(open_exit_frames_after)):
            next_open_exit_frame = int(np.nanmin(open_exit_frames_after))
        else:
            next_open_exit_frame = behav_frame_num
    
        # Must meet certain criteria to be classified as transition:
        # 1. Have been in closed arm for certain period of time (arm0_t_min)
        # 2. Must traverse center zone quickly (center_t_limit)
        # 3. Be in open arm for certain period of tie (arm1_t_min)
        if next_open_enter_frame < next_close_enter_frame and\
           frame - close_enter_frame > arm0_t_min * behav_fps and\
           next_open_enter_frame - frame < center_t_limit * behav_fps and\
           next_open_exit_frame - next_open_enter_frame > arm1_t_min * behav_fps:
            frame_start = frame - arm0_t_min * behav_fps
            frame_end = next_open_enter_frame + arm1_t_min * behav_fps
            close2open[frame_start:frame_end] = 1
            
        last_frame = frame
        # pdb.set_trace()
    
    # Find open-to-close transitions
    open2close = np.zeros(behav_frame_num)
    last_frame = 0
    for frame in open_exit_frames:
    
        # Frame of open-arm entry
        # If no frame exists, must have started in open arm, and first frame
        # should be used.
        open_enter_frames_before = np.where(frame - open_enter_frames > 0, open_enter_frames, 0)
        if np.any(open_enter_frames_before):
            open_enter_frame = np.max(open_enter_frames_before)
        else:
            open_enter_frame = 0
        
        # Frame of next open-arm entry
        open_enter_frames_after = np.where(open_enter_frames - frame > 0,
                                           open_enter_frames, np.nan)
        if np.any(np.isfinite(open_enter_frames_after)):
            next_open_enter_frame = int(np.nanmin(open_enter_frames_after))
        else:
            next_open_enter_frame = behav_frame_num
        
        # Frame of next closed-arm entry
        close_enter_frames_after = np.where(close_enter_frames - frame > 0,
                                            close_enter_frames, np.nan)              # first index in array of frame indices of open-arm entries after close-arm exit
        if np.any(np.isfinite(close_enter_frames_after)):
            next_close_enter_frame = int(np.nanmin(close_enter_frames_after))
        else:
            break
    
        # Frame of next closed-arm exit
        close_exit_frames_after = np.where(close_exit_frames - frame > 0,
                                           close_exit_frames, np.nan)
        if np.any(np.isfinite(close_exit_frames_after)):
            next_close_exit_frame = int(np.nanmin(close_exit_frames_after))
        else:
            next_close_exit_frame = behav_frame_num
    
        # Must meet certain criteria to be classified as transition:
        # 1. Have been in closed arm for certain period of time (arm0_t_min)
        # 2. Must traverse center zone quickly (center_t_limit)
        # 3. Be in open arm for certain period of tie (arm1_t_min)
        if next_close_enter_frame < next_open_enter_frame and\
           frame - open_enter_frame > arm0_t_min * behav_fps and\
           next_close_enter_frame - frame < center_t_limit * behav_fps and\
           next_close_exit_frame - next_close_enter_frame > arm1_t_min * behav_fps:
            frame_start = frame - arm0_t_min * behav_fps
            frame_end = next_close_enter_frame + arm1_t_min * behav_fps
            open2close[frame_start:frame_end] = 1
            
        last_frame = frame
    
    # Add new data
    data_norm2[d] = np.column_stack((data_norm2[d], close_enter_mask,
                                                    close_exit_mask,
                                                    open_enter_mask,
                                                    open_exit_mask,
                                                    close2open,
                                                    open2close))

# Add labels of new variables
labels_final = labels + ['closed_arm_entrance',
                         'closed_arm_exit',
                         'open_arm_entrance',
                         'open_arm_exit',
                         'closed_to_open_transition',
                         'open_to_closed_transition']


#%% Downsample to match calcium imaging time
# NOTE: data_ds is var x time (row-major order)

data_ds = []

for d in np.arange(file_num):
   # Find matching "bin" in calcium imaging time for each time point of behavioral data
   bin_ix = np.digitize(data_norm2[d][:, time_ix], sig_ts)
   
   data_ds.append(np.nan * np.zeros((len(labels_final), sig_frame_num), dtype=float))
   # Downsample
   for dbin in np.arange(sig_frame_num):
       bin_pts = np.where(bin_ix == dbin+1)[0]
       if bin_pts.size:
           data_ds[d][:, dbin] = np.mean(data_norm2[d][bin_pts, :], axis=0)
    

#%% GLM - Clean and select data

selected_behavs = ['Velocity',
                   'In_zoneCenter__centerpoint',
                   'In_zoneOpen_arms__centerpoint',
                   'In_zoneClosed_arms__centerpoint',
                   'Mobility_stateHighly_mobile',
                   'Mobility_stateMobile',
                   'Mobility_stateImmobile',
                   'Distance_to_zone',
                   'closed_to_open_transition',
                   'open_to_closed_transition']
other_vars = ['Distance_to_zone * In_zoneOpen_arms__centerpoint',
              'Distance_to_zone * In_zoneClosed_arms__centerpoint']
num_vars = len(selected_behavs) + len(other_vars) + 3
formula = [[]] * file_num
valid_frames = [[]] * file_num

for d in np.arange(len(data_ds)):
    # Remove time points with missing data
    valid_frames[d] = np.where(np.isfinite(data_ds[d][time_ix, :]))[0]
    
    # Select behavioral variables
#    valid_behav_ix = np.ones(len(selected_behavs.shape, dtype=bool))
#    print "Behaviral variables chosen are:"
#    for n, behav in enumerate(selected_behavs):
#        if np.any(data_ds[d][valid_frames[d], labels_final.index(behav)]):
#            print " + " + behav
#        else:
#            print " - " + behav + ": chosen but does not vary, thus omitted"
#            valid_behav_ix[n] = False
    
    # Define formula for subject
#    formula[d] = 'signal ~ const' + ' + '.join(selected_behavs[valid_behav_ix])\
    formula[d] = 'signal ~ signal_tminus1 + signal_tminus2 + ' +\
                 ' + '.join(selected_behavs + other_vars)


#%% GLM - Model

plt.ioff()
plot_dir = os.path.join(save_dir, 'plots')
if not os.path.isdir(plot_dir):
    os.makedirs(plot_dir)

num_cells = sum(num_cells_per_subj)

p_vals = np.zeros((num_cells, num_vars))
coeffs = np.zeros((num_cells, num_vars))
r_sq = np.zeros((num_cells, 2))

for cell in np.arange(num_cells):
    sig_subj_id = sigs_id[cell]
    subj_ix = behav_id_ord.index(sig_subj_id)
    
    # Behavioral data
    df = pd.DataFrame(data=data_ds[subj_ix][:, valid_frames[subj_ix]].T, columns=labels_final)
#    df = sm.add_constant(df)

    # Setup variables    
    y = sigs_z[cell, valid_frames[subj_ix]]
    df['signal'] = y
    df['signal_tminus1'] = np.concatenate((np.nan * np.ones(1), y[:-1]))
    df['signal_tminus2'] = np.concatenate((np.nan * np.ones(2), y[:-2]))
    
    # Signal lagging by frame
#    df['signal'] = np.concatenate((np.nan * np.ones(1), y[:-1]))
#    df['signal_tminus1'] = np.concatenate((np.nan * np.ones(2), y[:-2]))
#    df['signal_tminus2'] = np.concatenate((np.nan * np.ones(3), y[:-3]))
    
    # Run OLS
    model = smf.ols(data=df, formula=formula[subj_ix]).fit()  # first two frames has nan for signal_tminus2
    r_sq[cell, :] = [model.rsquared, model.rsquared_adj]
    p_vals[cell, :] = model.pvalues
    coeffs[cell, :] = model.params
    
    # Plot
    prstd, iv_l, iv_u = wls_prediction_std(model)

    fig, ax  = plt.subplots(2, 1)
    fig.suptitle("Cell " + str(cell))
    
    ax[0].plot(df['signal'], 'b-', label="data")
    ax[0].plot(model.fittedvalues, 'r-')
    ax[0].plot(iv_l, 'r--')
    ax[0].plot(iv_u, 'r--')
    ax[0].set_ylabel("Model (& observed) fluorescence")
    ax[0].xaxis.set_visible(False)
    
    ax[1].plot(df['signal'] - model.fittedvalues, 'g-')
    ax[1].set_ylabel("Fluoresence residual")
    ax[1].set_xlabel("Time")

    plt.savefig(os.path.join(plot_dir, str(cell) + '.png'),
                dpi=200, bbox_inches='tight')
    plt.close()

np.savetxt(os.path.join(save_dir, 'r_sq.txt'), r_sq)
np.savetxt(os.path.join(save_dir, 'p_values.txt'), p_vals)
np.savetxt(os.path.join(save_dir, 'coeffs.txt'), coeffs)
np.savetxt(os.path.join(save_dir, 'formula.txt'), formula, fmt='%s')


#%% GLM - Identify neurons significantly affected by each variable

sig_ix = bh_correction(p_vals.flatten())
sig_i, sig_j = np.unravel_index(sig_ix, p_vals.shape)

# Mask of significant variables for each neuron
sig_mask = np.zeros(p_vals.shape, dtype=bool)
sig_mask[sig_i, sig_j] = True

# Mask of positively and negatively significant variables
pos_sig_mask = np.logical_and(sig_mask, coeffs > 0)
neg_sig_mask = np.logical_and(sig_mask, coeffs < 0)

# Mask of significant varialbes with +1 and -1 for positve and negative
# relationship
dir_mask = np.zeros(coeffs.shape, dtype=int)
dir_mask[pos_sig_mask] = 1
dir_mask[neg_sig_mask] = -1


#%% Plot coefficients
from mpl_toolkits.mplot3d import Axes3D

fig = plt.figure()
ax = fig.add_subplot(111, projection='3d')
colors = ['blue', 'gray', 'red']
ref = 11 # color coded by closed to open

for cell_response in [-1, 0, 1]:
    mask = dir_mask[:, ref] == cell_response
    x0 = coeffs[mask, 11] # closed to open
    x1 = coeffs[mask, 12] # open to closed
    x2 = coeffs[mask, 13] # distance into open arm
    
    ax.scatter(x0, x1, x2, c=colors[cell_response+1], s=50)

ax.set_xlabel("Closed to open")
ax.set_ylabel("Open to closed")
ax.set_zlabel("Distance into open arm")
plt.show()


#%% Event focused - Create windows for arm transitions for one animal (for EPM)

animal = 0  # animal number
animal_ix = sigs_id == sig_id_ord[animal]
sigs_animal = sigs_z[animal_ix, :]
num_cells, num_frames = sigs_animal.shape

pre_frame_num = 25
post_frame_num = 25
frame_dur = 0.2  # Time between frame starts

all_data = data_ds[animal]

var_names = labels_final

# Index variables
close2open_ix = var_names.index('closed_to_open_transition')
open2close_ix = var_names.index('open_to_closed_transition')
from_close_ix = var_names.index('closed_arm_exit')
from_open_ix = var_names.index('open_arm_exit')
to_close_ix = var_names.index('closed_arm_entrance')
to_open_ix = var_names.index('open_arm_entrance')

# Index frames
close2open_frames = all_data[close2open_ix, :] > 0
open2close_frames = all_data[open2close_ix, :] > 0
close_exit_frames_all = all_data[from_close_ix, :] > 0
open_exit_frames_all = all_data[from_open_ix, :] > 0
close_enter_frames_all = all_data[to_close_ix, :] > 0
open_enter_frames_all = all_data[to_open_ix, :] > 0

# Events during transition
# eg, close_exit_frames are frames where subject exits closed arm during 
# closed=to-open arm transitions
close_exit_frames = np.logical_and(close_exit_frames_all, close2open_frames)
open_exit_frames = np.logical_and(open_exit_frames_all, open2close_frames)
close_enter_frames = np.logical_and(close_enter_frames_all, open2close_frames)
open_enter_frames = np.logical_and(open_enter_frames_all, close2open_frames)

# Create and plot arm transition windows

exit_frames = [close_exit_frames, open_exit_frames]
enter_frames = [open_enter_frames, close_enter_frames]
transition = ["Closed-to-open", "Open-to-closed"]

for tt in np.arange(2):
    if np.any(exit_frames[tt]):
        
        # Create windows around arm transitions
        pre_sig = np.stack([sigs_animal[:, ix-pre_frame_num:ix] for ix in np.where(exit_frames[tt])[0]], axis=-1)
        post_close2open = np.stack([sigs_animal[:, ix:ix+post_frame_num] for ix in np.where(enter_frames[tt])[0]], axis=-1)
        
        # Average time series response
        pre_sig_trial_avg = pre_sig.mean(axis=2)
        post_sig_trial_avg = post_close2open.mean(axis=2)
        
        # Average epoch response per cell
        pre_sig_epoch_avg = pre_sig_trial_avg.mean(axis=1)
        post_sig_epoch_avg = post_sig_trial_avg.mean(axis=1)
        
        
        # Plot
        fig = plt.figure()
        gs = gridspec.GridSpec(2, 3)
        cell = 0   # example cell to plot
        color_pre = 'lightsteelblue'
        color_post = 'steelblue'
        c_palette = sns.color_palette([color_pre, color_post])
        
        # Plot data from all cells (averaged over trials)
        x0, y0 = np.meshgrid(np.arange(0, -(pre_frame_num+1), -1) * frame_dur,
                             np.arange(num_cells+1))
        x1, y1 = np.meshgrid(np.arange(post_frame_num+1) * frame_dur,
                             np.arange(num_cells+1))
        max_z = np.concatenate((pre_sig_trial_avg.flatten(),
                                post_sig_trial_avg.flatten())).max()
        min_z = np.concatenate((pre_sig_trial_avg.flatten(),
                                post_sig_trial_avg.flatten())).min()
        
        ax_pre_cmap = plt.subplot(gs[0, 0])
        ax_pre_cmap.set_title("{} transitions".format(labels[tt]))
        im_pre = ax_pre_cmap.pcolormesh(x0, y0, pre_sig_trial_avg)
        im_pre.set_clim([min_z, max_z])
        ax_pre_cmap.set_ylabel("Cells")
        ax_pre_cmap.set_xlabel('Pre')
        
        ax_post_cmap = plt.subplot(gs[0, 1])
        im_post = ax_post_cmap.pcolormesh(x1, y1, post_sig_trial_avg)
        im_post.set_clim([min_z, max_z])
        ax_post_cmap.yaxis.set_visible(False)
        ax_post_cmap.set_xlabel("Post")
    
        # Plot average (for example cell)
        x0 = np.arange(0, -pre_frame_num, -1) * frame_dur
        x1 = np.arange(post_frame_num) * frame_dur
        y0 = pre_sig_trial_avg.mean(axis=0)
        y1 = post_sig_trial_avg.mean(axis=0)
        e0 = stats.sem(pre_sig_trial_avg, axis=0)
        e1 = stats.sem(post_sig_trial_avg, axis=0)
        ymax = np.amax(np.concatenate((y0, y1))) + np.amax(np.concatenate((e0, e1)))
        ymin = np.amin(np.concatenate((y0, y1))) - np.amin(np.concatenate((e0, e1)))
        
        ax_pre_sig = plt.subplot(gs[1, 0])
        ax_pre_sig.plot(x0, y0, color_pre)
        ax_pre_sig.fill_between(x0, y0-e0, y0+e0,
                                facecolor=color_pre, alpha=0.3)
        ax_pre_sig.set_ylabel('Fluorescence value')
        ax_pre_sig.set_ylim(ymin, ymax)    
        ax_pre_sig.set_xlabel('Pre')
        
        ax_post_sig = plt.subplot(gs[1, 1])
        ax_post_sig.plot(x1, y1, color_post)
        ax_post_sig.fill_between(x1, y1-e1, y1+e1,
                                facecolor=color_post, alpha=0.3)
        ax_post_sig.set_ylim(ymin, ymax)
        ax_post_sig.yaxis.set_visible(False)
        ax_post_sig.set_xlabel('Post')
        
        # Plot average pre/post
        w = 0.8
        x = np.arange(2)
        y = [pre_sig_epoch_avg.mean(),
             post_sig_epoch_avg.mean()]
        e = [stats.sem(pre_sig_epoch_avg),
             stats.sem(pre_sig_epoch_avg)]
        
        ax_avg_sig = plt.subplot(gs[1, 2])
        error_config = {'ecolor': '0.3'}
        ax_avg_sig.bar(x-w/2, y, yerr=e, error_kw=error_config,
                  fill=False)
        sns.swarmplot(np.repeat(x, num_cells),
                      np.concatenate([pre_sig_epoch_avg, post_sig_epoch_avg]),
                      ax=ax_avg_sig,
                      palette=c_palette)
        ax_avg_sig.set_title("Average response")
        ax_avg_sig.set_ylim(ymin, ymax)
        ax_avg_sig.yaxis.set_visible(False)
        ax_avg_sig.set_xticklabels(('Pre', 'Post'))
        
        gs.tight_layout(fig)
        
        # Save data
        if event_data:
            data_type = "events"
        else:
            data_type = "traces"
        np.savetxt(os.path.join(save_dir, '{}_transitions_{}.txt'.format(transition[tt], data_type)),
                   post_sig_trial_avg, delimiter = '\t')


#%% Calculate preference index

open_col = labels.index('In_zoneOpen_arms__centerpoint')
closed_col = labels.index('In_zoneClosed_arms__centerpoint')

pref_index_avg = [[]] * file_num
pref_index_ev = [[]] * file_num
pref_index_avg_p = [[]] * file_num
pref_index_ev_p = [[]] * file_num
for f in np.arange(file_num):
    cell_ix = np.where(sig_id == f)[0]
    closed_frames = data_ds[f][closed_col, :] > 0
    open_frames = data_ds[f][open_col, :] > 0
    
    sig_temp = sigs_animal[cell_ix, :]
    sig_closed = sig_temp[:, closed_frames]
    sig_open = sig_temp[:, open_frames]
    pref_index_avg[f] = np.log(sig_open.mean(axis=1)/sig_closed.mean(axis=1))
    pref_index_avg_p[f] = np.array([stats.mannwhitneyu(x, y)[1] for x, y in zip(sig_closed, sig_open)])

    np.savetxt(os.path.join(save_dir, 'preference_indices_by_avg_{}.txt'.format(f)),
               np.concatenate(pref_index_avg), delimiter = '\t')
    np.savetxt(os.path.join(save_dir, 'preference_indices_by_events_{}.txt'.format(f)),
               np.concatenate(pref_index_ev), delimiter = '\t')
    np.savetxt(os.path.join(save_dir, 'preference_indices_by_avg_p_{}.txt'.format(f)),
               np.concatenate(pref_index_avg_p), delimiter = '\t')
    np.savetxt(os.path.join(save_dir, 'preference_indices_by_events_p_{}.txt'.format(f)),
               np.concatenate(pref_index_ev_p), delimiter = '\t')


#%% Create activity map

x = data_ds[0][2, :]
y = data_ds[0][3, :]
c, _ = activity_map(x, y, sigs_animal[0, :], binsize=0.025, plot=True, sigma=2)