"""
ROULS - DREEM processing module

Purpose: Functions for processing outputs from DREEM
Author: Matty Allan
Modified: 2021-05-26
"""

from html.parser import HTMLParser
import itertools
import json
import logging
import os
import re
import sys
from tqdm import tqdm

import matplotlib
matplotlib.rcParams["font.family"] = "Arial"
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
from matplotlib import pyplot as plt
import numpy as np
import pandas as pd
from scipy.spatial import distance
from scipy.stats import pearsonr, rankdata, spearmanr, mannwhitneyu
from sklearn.metrics import roc_curve, roc_auc_score

sys.path = [os.path.dirname(__file__)] + sys.path
from seqs import get_ac_positions, read_fasta 

from struct_utils import read_ct_file_single
from struct_utils import plot_arc

POP_AVG_MM = "Mismatches"
POP_AVG_MMDEL = "Mismatches + Deletions"
POP_AVG_INDEX_COL = "Position"
CLUSTERS_MU_HEADER_LINES = 2
CLUSTERS_MU_INDEX_COL = "Position"


def remove_gu(data, seq, start_pos=1, warn_nonzero_gu=True,
        warn_zero_ac=False, missing_seq="raise"):
    """
    Remove entries in data corresponding to Gs and Us in seq.
    params:
    - data (pd.Series/pd.DataFrame): the data from which to remove Gs and Us,
      where the index values are positions in the sequence
    - seq (str): the sequence
    - start_pos (int): numeric position to assign the first base in seq
    - warn_nonzero_gu (bool): whether to warn if any G/U position have non-zero
      data
    - missing_seq (str): if any positions in data are missing in the sequence,
      whether to 'raise' an error or 'drop' the missing positions
    returns:
    - data_ac (pd.Series/pd.DataFrame): the input data without rows
      corresponding to Gs and Us.
    """
    # If Gs and Us are not included, remove them.
    if seq is None:
        raise ValueError("seq must be given if omitting Gs and Us")
    # Check that all of the indexes lie within the sequence.
    min_data_index = data.index.min()
    max_data_index = data.index.max()
    end_pos = len(seq) + start_pos - 1
    if min_data_index < start_pos or max_data_index > end_pos:
        if missing_seq == "raise":
            raise ValueError(f"data (indexes {min_data_index} - {max_data_index})"
                             f" includes indexes not in sequence"
                             f" (indexes {start_pos} - {end_pos})")
        elif missing_seq == "drop":
            data = data.loc[start_pos: end_pos, :]
        else:
            raise ValueError(f"invalid value for missing_seq: '{missing_seq}'")
    # Find the positions of all As and Cs.
    ac_pos = set(get_ac_positions(seq, start_pos=start_pos))
    # Keep only positions that both have DMS data and are an A or C.
    if warn_nonzero_gu:
        data_ac_pos = list()
        data_gu_pos = list()
        for pos in data.index:
            if pos in ac_pos:
                data_ac_pos.append(pos)
            else:
                data_gu_pos.append(pos)
        if isinstance(data, pd.Series):
            data_ac = data.loc[data_ac_pos]
            data_gu = data.loc[data_gu_pos]
        elif isinstance(data, pd.DataFrame):
            data_ac = data.loc[data_ac_pos, :]
            data_gu = data.loc[data_gu_pos, :]
        else:
            raise ValueError("data must be pandas Series or DataFrame, not"
                    f" '{type(data)}'")
        # Warn if any GU positions have non-zero signals.
        if isinstance(data, pd.Series):
            nonzero_gu = int(np.sum(np.logical_not(
                    np.logical_or(np.isclose(data_gu, 0.0), np.isnan(data_gu)))))
        elif isinstance(data, pd.DataFrame):
            nonzero_gu = int(np.sum(np.sum(np.logical_not(
                    np.logical_or(np.isclose(data_gu, 0.0), np.isnan(data_gu))))))
        else:
            raise ValueError("data must be pandas Series or DataFrame, not"
                    f" '{type(data)}'")
        if nonzero_gu > 0:
            logging.warning(f"data contains {nonzero_gu} non-zero DMS"
                             " signals in G/U positions.")
    else:
        data_ac_pos = data.index.isin(ac_pos)
        if isinstance(data, pd.Series):
            data_ac = data.loc[data_ac_pos]
        elif isinstance(data, pd.DataFrame):
            data_ac = data.loc[data_ac_pos, :]
        else:
            raise ValueError("data must be pandas Series or DataFrame, not"
                    f" '{type(data)}'")
    # Warn if any kept (discarded) positions have zero (nonzero) signals.
    zero_ac = int(np.sum(np.isclose(data_ac, 0.0)))
    if zero_ac > 0 and warn_zero_ac:
        logging.warning(f"data contains {zero_ac} zero-valued DMS"
                         " signals in A/C positions.")
    return data_ac


def read_pop_avg(pop_avg_file, mm=False, mmdel=True, include_gu=True, seq=None,
                 start_pos=1):
    """
    Read mutation rates from a population average format file.
    params:
    - pop_avg_file (str): path to popavg_reacts.txt
    - include_gu (bool): whether to include G/U bases in the returned mutation
      rates (default) or not. If False, seq must be given, and if any G/U (A/C)
      positions have non-zero (zero) values, then a warning is logged.
    - seq (str): sequence corresponding to the mutation rates. The first base in
      the sequence is considered to be at position 1, and the positions in the
      Clusters_Mu.txt file must match this numbering system. If include_gu is
      False, seq must be given to determine which bases are G/U; otherwise, seq
      has no effect.
    - start_pos (int): the number to give the first base in the sequence
    - mm (bool): whether to return mismatch-only rates
    - mmdel (bool): whether to return mismatch+deletion rates
    returns:
    - mus (pd.DataFrame/pd.Series): the mutation rates at each position (index)
      in each cluster (column)
    """
    pop_avg = pd.read_csv(pop_avg_file, sep="\t", index_col=POP_AVG_INDEX_COL)
    pop_avg.drop(columns="Base", inplace=True)
    if not include_gu:
        pop_avg = remove_gu(pop_avg, seq, start_pos, warn_nonzero_gu=False)
    if mm and mmdel:
        return pop_avg
    elif mmdel:  # and not mm
        return pop_avg[POP_AVG_MMDEL]
    elif mm:  # and not mmdel
        return pop_avg[POP_AVG_MM]
    else:
        raise ValueError("At least one of mm or mmdel must be True.")


def read_clusters_mu(clusters_mu_file, flatten=False, include_gu=True, seq=None,
                     start_pos=1, missing_seq="raise"):
    """
    Read the by-cluster mutation rates from a Clusters_Mu.txt file.
    params:
    - clusters_mu_file (str): path to Clusters_Mu.txt
    - flatten (bool): if there is only one cluster, whether to flatten to Series
      or keep as DataFrame (default)
    - include_gu (bool): whether to include G/U bases in the returned mutation
      rates (default) or not. If False, seq must be given, and if any G/U (A/C)
      positions have non-zero (zero) values, then a warning is logged.
    - seq (str): sequence corresponding to the mutation rates. The first base in
      the sequence is considered to be at position 1, and the positions in the
      Clusters_Mu.txt file must match this numbering system. If include_gu is
      False, seq must be given to determine which bases are G/U; otherwise, seq
      has no effect.
    - start_pos (int): the number to give the first base in the sequence
    returns:
    - mus (pd.DataFrame/pd.Series): the mutation rates at each position (index)
      in each cluster (column)
    """
    mus = pd.read_csv(clusters_mu_file, sep="\t",
                      skiprows=CLUSTERS_MU_HEADER_LINES,
                      index_col=CLUSTERS_MU_INDEX_COL)
    # Rename the colums from "Cluster_1", "Cluster_2" ... to 1, 2 ...
    mus.columns = [col.split("_")[1].strip() for col in mus.columns]
    if not include_gu:
        mus = remove_gu(mus, seq, start_pos, warn_nonzero_gu=True,
                missing_seq=missing_seq)
    # If there is only one column, optionally flatten data to a Series.
    if flatten and len(mus.columns) == 1:
        return mus.squeeze()
    else:
        return mus


def read_plain_mu_file(filename, include_gu=True, seq=None,
        start_pos=1, flatten=False, sep="\t", has_index=True, has_header=False,
        drop_negatives=True, check_bounds=True):
    if has_header:
        header = "infer"
    else:
        header = None
    if has_index:
        index_col = 0
    else:
        index_col = None
    mus = pd.read_csv(filename, sep=sep, header=header, index_col=index_col)
    if not has_index:
        mus.index = list(range(1, len(mus.index) + 1))
    if drop_negatives:
        mus[mus < 0.0] = np.nan
    if check_bounds:
        if np.any(mus > 1.0) or np.any(mus < 0.0):
            raise ValueError("mus out of range [0, 1]")
    if not include_gu:
        mus = remove_gu(mus, seq, start_pos=start_pos, warn_nonzero_gu=True)
    if flatten and len(mus.columns) == 1:
        return mus.squeeze()
    else:
        return mus


def read_mutation_rates(filename, include_gu=True, seq=None,
        start_pos=1, flatten=False, mmdel=True, mm=False):
    if not os.path.isfile(filename):
        raise FileNotFoundError(filename)
    mus = None
    if mus is None:
        try:
            mus = read_clusters_mu(filename, flatten=flatten,
                    include_gu=include_gu, seq=seq, start_pos=start_pos)
        except:
            pass
    if mus is None:
        try:
            mus = read_pop_avg(filename, mmdel=mmdel, mm=mm,
                     include_gu=include_gu, seq=seq, start_pos=start_pos)
        except:
            pass
    if mus is None:
        try:
            mus = read_plain_mu_file(filename, include_gu=include_gu, seq=seq,
                    start_pos=1, flatten=flatten, sep="\t", has_index=True,
                    has_header=False)
        except:
            pass
    if mus is None:
        raise ValueError(f"Cannot read {filename}")
    return mus


def read_coverage(coverage_file):
    """
    Read the read coverage from an HTML file.
    """
    coverage = None
    cov_start_str = '[{"type": "bar", "x": ['
    cov_end_str = "]}]"
    n_start_str = "Number of bit vectors: "
    n_end_str = '"},'

    with open(coverage_file) as f:
        contents = f.read()

    if contents.count(n_start_str) == 1:
        n_start = contents.index(n_start_str)
        n_end = contents[n_start:].index(n_end_str) + len(n_end_str) \
                + n_start
        n_text = contents[n_start + len(n_start_str):
                n_end - len(n_end_str)]
        n_bitvectors = int(n_text)
    else:
        raise ValueError(f"found {contents.count(n_start_str)}"
                " bit vector count reports")

    if contents.count(cov_start_str) == 1:
        cov_start = contents.index(cov_start_str)
        cov_end = contents[cov_start:].index(cov_end_str) \
                + len(cov_end_str) + cov_start
        json_text = contents[cov_start: cov_end]
        json_eval = json.loads(json_text)[0]
        pos = np.asarray(json_eval["x"], dtype=np.int)
        coverage = np.round(
                np.asarray(json_eval["y"]) * n_bitvectors,
                0).astype(np.int)
        coverage = pd.Series(coverage, index=pos)
    else:
        raise ValueError(f"found {contents.count(cov_start_str)}"
                " bit vector count reports")
    return coverage


def mu_histogram(filename, mus, labels=None, label_order=None,
        bin_width=None, n_bins=None,
        xlabel="DMS signal", ylabel="frequency",
        xmax=None, ymax=None, vertical=True):
    """
    Draw a histogram of mutation rates.
    """
    if isinstance(mus, pd.DataFrame):
        pass
    elif isinstance(mus, pd.Series):
        mus = mus.to_frame()
    else:
        mus = pd.DataFrame(np.asarray(mus))
    n_bases, n_plots = mus.shape
    if n_bins is None:
        if bin_width is None:
            bin_width = 0.005
        bin_limit = bin_width * np.ceil(mus.max().max() / bin_width)
        n_bins = int(round(bin_limit / bin_width))
    else:
        bin_limit = mus.max().max()
    hist_bins = np.linspace(0.0, bin_limit, n_bins + 1)
    if vertical:
        fig, axs = plt.subplots(nrows=n_plots, sharex=True, sharey=False, 
                squeeze=False)
        axs = [ax[0] for ax in axs]
    else:
        fig, axs = plt.subplots(ncols=n_plots, sharex=True, sharey=False,
                squeeze=False)
        axs = axs[0]
    if labels is not None:
        if isinstance(labels, pd.DataFrame):
            pass
        elif isinstance(labels, pd.Series):
            labels = labels.to_frame()
        if isinstance(labels, pd.DataFrame):
            common_indexes = sorted(set(labels.index) & set(mus.index))
            labels = labels.loc[common_indexes]
            mus = mus.loc[common_indexes]
        else:
            labels = pd.DataFrame(np.asarray(labels), index=mus.index)
        if labels.shape != mus.shape:
            raise ValueError("labels and mus must have same shape")
        if label_order is None:
            labels_set = sorted({label for col in labels 
                    for label in labels[col]})
        else:
            if labels.isin(label_order).all().all():
                labels_set = {label for col in labels 
                        for label in labels[col]}
                labels_set = [label for label in label_order
                        if label in labels_set]
            else:
                raise ValueError("All labels must be in label_order")
        n_labels = len(labels_set)
        all_label_mus = dict()
        for i_plot, ax in enumerate(axs):
            col = mus.columns[i_plot]
            all_label_mus[col] = dict()
            for label in labels_set:
                label_mus = mus.iloc[:, i_plot].loc[
                        labels.iloc[:, i_plot] == label].squeeze()
                ax.hist(label_mus, alpha=1/n_labels, label=str(label),
                        bins=hist_bins)
                ax.set_title(col)
                all_label_mus[col][label] = label_mus
        plt.legend()
    else:
        for i_plot, ax in enumerate(axs):
            ax.hist(mus.iloc[:, i_plot].squeeze())
            ax.set_title(mus.columns[i_plot])
    if xmax is not None:
        plt.xlim((0.0, xmax))
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.savefig(filename)
    plt.close()
    return all_label_mus


def mu_histogram_paired(filename, mus, paired, **kwargs):
    labels = {True: "paired", False: "unpaired"}
    if isinstance(paired, pd.Series):
        paired_labels = pd.Series([labels.get(x, "no data") for x in paired],
                index=paired.index)
    elif isinstance(paired, pd.DataFrame):
        paired_labels = pd.DataFrame([
            [labels.get(paired.loc[row, col], "no data") 
                for col in paired.columns]
            for row in paired.index],
            index=paired.index)
    label_order = ["paired", "unpaired", "no data"]
    all_label_mus = mu_histogram(filename, mus, labels=paired_labels,
            label_order=label_order, **kwargs)
    aurocs = dict()
    u_stats = dict()
    p_vals = dict()
    for col, col_label_mus in all_label_mus.items():
        result = mannwhitneyu(col_label_mus["paired"],
                col_label_mus["unpaired"], use_continuity=True)
        p_val = result.pvalue
        u_stat = result.statistic
        u_max = len(col_label_mus["paired"]) * len(col_label_mus["unpaired"])
        auroc = 1 - u_stat / u_max
        aurocs[col] = auroc
        u_stats[col] = u_stat
        p_vals[col] = p_val
    return all_label_mus, aurocs, u_stats, p_vals

    
def get_data_structure_agreement(metric, paired, mus, weights=None,
        min_paired=None, min_unpaired=None, check_indexes=True):
    """
    Compute the data-structure agreement (DSA), a non-parametric value
    quantifying how well a structure is supported by mutation rate data.
    Meaning:
    - Take the fraction of all possible combinations of one paired and one
    unpaired base in which the unpaired base has a higher mutation rate and
    subtract the fraction in which the paired base has a higher mutation rate.
    Combinations in which both bases have the same mutation rate count towards
    the denominator but not the numerator.
    - There are two ways to measure the correlation:
      - The area under the receiver operating characteristic curve (AUROC)
        is the fraction of combinations in which the unparied base has a higher
        mutation rate and ranges from 1 (perfect agreement) to 0 (perfect
        disagreement), with 0.5 indicating no relationship. What is the ROC
        in this case? If one were to sweep a threshold from 0 to 1, and at each
        level of the threshold, predict that all bases with DMS < threshold are
        paired and all bases with DMS > threshold unpaired, then compute the
        true positive rate (TPR) and false positive rate (FPR) w.r.t. the actual
        pairing, then TPR vs FPR forms the ROC.
      - The rank biserial correlation (RBC) is the signed correlation between
        the structure and data, equal to 2 * AUROC + 1, which ranges from
        1 (perfect agreement) to -1 (perfect disagreement), with 0 indicating
        no relationship.
    - A low DSA (roughly AUROC < 0.75 or RBC < 0.5) indicates that the structure
    model is not well supported by the data (perhaps due to low-quality data or
    to long-distance interactions or alternative structures that are not
    properly modeled.
    - A high DSA (roughly AUROC > 0.9 or RBC > 0.8) does not confirm that the
    structure is correct, but in general the accepted models of structures have
    high RBC values.
    Caveats and notes:
    - In this implementation, bases with nan values of mutation rates or pairing
    status are ignored (assuming check_indexes=True), so they contribute to
    neither the numerator nor denominator.
    - DSA is ill-defined if either the number of paired bases (Np) or unpaired
    bases (Nu) is small. The maximum influence a single base can have on AUROC
    (i.e. if one were to vary its mutation rate from the lowest to the highest
    among all mutation rates while keeping the other bases fixed) is 1/Np for
    each paired base and 1/Nu for each unpaired base. One could compute a
    confidence interval for AUROC using bootstrapping or by finding an interval
    for the closely related Mann Whitney U1 and then converting to AUROC like so:
    AUROC = U1 / (Np * Nu)
    - In its basic form, DSA does not distinguish between unpaired bases with
    low signal (which are not uncommon, so shouldn't be penalized much) and
    paired bases with high signal (which are very uncommon, so should be
    penalized a lot). For this purpose, one could weight more heavily bases
    with higher mutation rates. Of course, this approach introduces weight
    parameters that could make the model more accurate if implemented well but
    less accurate if implemented poorly.
    params:
    - metric (str): whether to return 'AUROC' or 'RBC'
    - paired (array-like[bool]): whether each base is paied (True/1) or
      unpaired (False/0); must have same shape as mus
    - mus (array-like[float]): mutation rate of each base; must have same shape
      as argument unpaired
    - weights (array-like[float]): weights for weighted AUROC
    - min_paired (int): smallest number of paired bases permitting real result
    - min_unpaired (int): smallest number of unpaired bases permitting real
      result
    - check_indexes (bool): ensure that mus and unpaired indexes match; only set
      to False if the indexes have been validated by another function, e.g. one
      of the windows functions below.
    returns:
    - dsa (float): data-structure correlation measured by AUROC or RBC
    """
    metric = metric.strip().upper()
    # Convert paired and mus to pd.Series.
    if not isinstance(paired, pd.Series):
        paired = pd.Series(np.asarray(paired))
    if not isinstance(mus, pd.Series):
        mus = pd.Series(np.asarray(mus))
    if weights is not None:
        if not isinstance(weights, pd.Series):
            weightss = pd.Series(np.asarray(weights))
    if check_indexes:
        # Keep only the indexes that have data in both mus and paired.
        common_indexes = sorted(set(paired.index[~paired.isnull()]) &
                                set(mus.index[~mus.isnull()]))
        paired = paired.loc[common_indexes]
        mus = mus.loc[common_indexes]
        if weights is not None:
            weights = weights.loc[common_indexes]
    if paired.shape != mus.shape:
        raise ValueError("paired and mus must have same shape")
    if weights is not None:
        if weights.shape != paired.shape:
            raise ValueError("weights and paired must have same shape")
    if len(paired.shape) != 1:
        raise ValueError("paired and mus must be 1-dimensional")
    n_bases = paired.shape[0]
    # Cast remaining values to appropriate data types.
    if paired.dtype is not np.bool:
        paired = paired.astype(np.bool)
    n_paired = np.sum(paired)
    n_unpaired = n_bases - n_paired
    n_grid = n_paired * n_unpaired
    if min_paired is None:
        min_paired = 10
    if min_unpaired is None:
        min_unpaired = 10
    if n_paired < min_paired or n_unpaired < min_unpaired or n_grid == 0:
        # If there are insufficient bases to compute AUROC, return nan.
        logging.warning(f"insufficient numbers of paired ({n_paired})"
                f" or unpaired ({n_unpaired}) bases for {metric} calculation")
        auroc = np.nan
    else:
        # Compute the AUROC.
        #auroc = roc_auc_score(np.logical_not(paired), mus,
        #        sample_weight=weights)
        ranks = rankdata(mus, method="average")
        u_stat = np.sum(ranks[paired]) - n_paired * (n_paired + 1) / 2
        auroc = 1 - u_stat / n_grid
    if metric == "AUROC":
        return auroc
    elif metric == "RBC":
        # Convert AUROC to RBC.
        return auroc_to_rbc(auroc)
    else:
        raise ValueError(f"Invalid metric: '{metric}'")


def auroc_to_rbc(auroc):
    """
    Convert AUROC to RBC.
    """
    rbc = 2.0 * auroc - 1.0
    return rbc


def rbc_to_auroc(rbc):
    """
    Convert RBC to AUROC.
    """
    auroc = (rbc + 1.0) / 2.0
    return auroc


def get_data_structure_agreement_windows(window_size, window_step, metric,
        paired, mus, weights=None, min_paired=None, min_unpaired=None):
    """
    Compute data-structure agreement (DSA) over a region using sliding
    windows.
    params:
    - window_size (int): size of sliding window
    - window_step (int): amount by which to slide the window between successive
      steps
    - metric - min_paired: see documentation for get_data_structure_agreement
    returns:
    - dsas (pd.Series[float]): series of DSA at each window, with multiindex
      of the starting and ending positions of each window
    """
    # Convert unpaired and mus to pd.Series.
    if (not isinstance(paired, pd.Series)) or (not isinstance(mus, pd.Series)):
        paired = pd.Series(np.asarray(paired))
        mus = pd.Series(np.asarray(mus))
    # Keep only the indexes that have data in both mus and paired.
    valid_indexes = sorted(set(paired.index[~paired.isnull()]) &
                            set(mus.index[~mus.isnull()]))
    paired = paired.loc[valid_indexes]
    mus = mus.loc[valid_indexes]
    first_idx = valid_indexes[0]
    last_idx = valid_indexes[-1]
    if paired.shape != mus.shape:
        raise ValueError("paired and mus must have same shape")
    if len(paired.shape) != 1:
        raise ValueError("paired and mus must be 1-dimensional")
    n_bases = paired.shape[0]
    if window_size > n_bases:
        raise ValueError("window_size cannot exceed length of index")
    if window_step > n_bases:
        raise ValueError("window_step cannot exceed length of index")
    n_bases = paired.shape[0]
    # Cast remaining values to appropriate data types.
    if paired.dtype is not np.bool:
        paired = paired.astype(np.bool)
    window_starts = np.arange(first_idx, last_idx - (window_size - 1) + 1,
            window_step, dtype=np.int)
    window_ends = window_starts + (window_size - 1)
    window_frames = list(zip(window_starts, window_ends))
    dsas = pd.Series(index=pd.MultiIndex.from_tuples(window_frames),
            dtype=np.float32)
    for win_s, win_e in tqdm(window_frames):
        dsa = get_data_structure_agreement(metric,
                paired.loc[win_s: win_e], mus.loc[win_s: win_e],
                weights=weights, min_paired=min_paired,
                min_unpaired=min_unpaired, check_indexes=False)
        dsas.loc[(win_s, win_e)] = dsa
    return dsas


def plot_data_structure_roc_curve(paired, mus, plot_file=None):
    """
    Plot an ROC curve for the data vs the structure.
    """
    if isinstance(paired, pd.Series):
        paired = paired.to_frame()
    if isinstance(mus, pd.Series):
        mus = mus.to_frame()
    if not isinstance(paired, pd.DataFrame):
        raise ValueError("paired must be pd.DataFrame")
    if not isinstance(mus, pd.DataFrame):
        raise ValueError("mus must be pd.DataFrame")
    if len(paired.columns) != len(mus.columns):
        raise ValueError("paried and mus must have the same number of columns")
    matched_columns = (paired.columns == mus.columns).all()
    models = paired.columns.tolist()
    fig, ax = plt.subplots()
    tprs = dict()
    fprs = dict()
    for i_model, model in enumerate(models):
        # Filter out positions with missing values in either data set.
        if matched_columns:
            mus_model = mus.loc[:, model]
            paired_model = paired.loc[:, model]
        else:
            mus_model = mus.iloc[:, i_model]
            paired_model = paired.iloc[:, i_model]
        valid_indexes = sorted(set(mus.index[~mus_model.isnull()]) &
                set(paired.index[~paired_model.isnull()]))
        paired_model = paired_model.loc[valid_indexes].astype(np.bool).values
        mus_model = mus_model.loc[valid_indexes].values
        # Compute FPR and TPR using sklearn.
        # mus are negated so that they are positively correlated with the
        # labels in paired (1 for paired, 0 for unpaired)
        #fpr, tpr, thresh = roc_curve(paired_model.values, -mus_model.values)
        # Determine the rank order of the mutation rates.
        rank_order = np.argsort(mus_model)
        # Sort all of the values by mutation rate.
        #mus_sorted = mus_model[rank_order]
        paired_sorted = paired_model[rank_order]
        # Compute the cumulative sum of paired bases.
        paired_cumsum = np.hstack([[0], np.cumsum(paired_sorted)])
        # The "true positive rate" (TPR) is the fraction of paired bases
        # correctly identified as paired (i.e. below the implicit threshold).
        n_paired = paired_cumsum[-1]
        tpr = paired_cumsum / n_paired
        # Compute the cumulative sum of unpaired bases by threshold.
        unpaired_cumsum = np.hstack([[0],
            np.cumsum(np.logical_not(paired_sorted))])
        # The "false positive rate" (FPR) is the fraction of unpaired bases
        # incorrectly identified as paired (i.e. below the implicit threshold).
        n_unpaired = unpaired_cumsum[-1]
        fpr = unpaired_cumsum / n_unpaired
        assert n_paired + n_unpaired == paired_model.shape[0]
        # Plot TPR vs FPR to get the ROC curve.
        if matched_columns:
            label = model
        else:
            label = i_model
        ax.plot(fpr, tpr, label=label)
        tprs[model] = tpr
        fprs[model] = fpr
        """
        # Compute area under ROC curve.
        weights = np.arange(len(paired_model))
        auroc = roc_auc_score(paired_model, -mus_model, sample_weight=weights)
        m_tpr = (tpr[:-1] + tpr[1:]) / 2
        d_fpr = np.diff(fpr)
        m_fpr = 1 - (fpr[:-1] + fpr[1:]) / 2
        d_tpr = np.diff(tpr)
        auroc2 = np.sum(m_tpr * d_fpr)
        auroc3 = np.sum(m_fpr * d_tpr)
        print(auroc1, auroc2, auroc3)
        input()
        """
    ax.plot([0, 1], [0, 1], c="gray", linestyle="--")
    ax.set_xlim((0, 1))
    ax.set_ylim((0, 1))
    ax.set_aspect(1.0)
    ax.set_xlabel("Unpaired Bases Below Threshold (FPR)")
    ax.set_ylabel("Paired Bases Below Threshold (TPR)")
    fig.set_size_inches((6, 6))
    fig.legend()
    plt.tight_layout()
    plt.savefig(plot_file)
    plt.close()
    return tprs, fprs


def read_bitvector_hist(bv_hist_file):
    """
    Return counts from a BitVectors_Hist.txt file.
    params:
    - bv_hist_file (str): path to BitVectors_Hist.txt file
    returns:
    - counts (dict[str, int]): counts of each item in the file
    """
    sep = ":"
    def process_line(line):
        """
        Parse a line from the file.
        params:
        - line (str): line from the file
        returns:
        - field (str): description of the bit vectors being counted
        - count (int): value of count
        """
        field, count = line.split(sep)
        count = int(count)
        return field, count
    with open(bv_hist_file) as f:
        counts = dict(map(process_line, f))
    return counts


def read_all_bitvector_hist_files(em_clustering_dir, missing="raise"):
    """
    Read all BitVectors_Hist.txt files in an EM_Clustering (or equivalent)
    directory.
    params:
    - em_clustering_dir (str): path to EM_Clustering (or equivalent) directory
    - missing (str): how to handle missing files; either "ignore" them or
      "raise" an error (default)
    returns:
    - counts (pd.DataFrame): counts where each row is a run (subdirectory) of
      em_clustering_dir and each column is a field in the file 
    """
    bv_hist_base = "BitVectors_Hist.txt"
    counts = dict()
    for directory in os.listdir(em_clustering_dir):
        # Loop through all subdirectories of em_clustering_dir.
        bv_hist_full = os.path.join(em_clustering_dir, directory, bv_hist_base)
        if os.path.isfile(bv_hist_full):
            # Read the file if it exists.
            counts[directory] = read_bitvector_hist(bv_hist_full)
        else:
            # If it does not exist, respond depending on missing.
            if missing == "raise":
                raise FileNotFoundError(f"no such file: {bv_hist_full}")
            elif missing == "ignore":
                continue
            else:
                raise ValueError(f"invalid value for missing: {missing}")
    # Reformat the counts into a DataFrame.
    counts = pd.DataFrame.from_dict(counts, orient="index")
    return counts


em_dirname_pattern = re.compile("(?P<sample>\S+)_(?P<ref>\S+)_(?P<start>\d+)"
        "_(?P<end>\d+)_InfoThresh-(?P<info>[0-9.]+)_SigThresh-(?P<sig>[0-9.]+)"
        "_IncTG-(?P<inctg>[A-Z]+)_DMSThresh-(?P<dms>[0-9]+)")

def get_sample_dirs(em_clustering_dir, sample="", ref="",
        start="", end="", info="", sig="", inctg="", dms="",
        multi="all"):
    """
    Given an EM_Clustering directory, return the subdirector(y/ies)
    that match the arguments. Leave an argument as an empty string to return
    subdirectories with any value of that argument.
    params:
    - em_clustering_dir (str): directory in which to search for samples
    - sample ... dms (str): values of sample, reference genome, start position,
      end position, info threshold, sig threshold, include TG, and DMS threshold
    - multi (str): how to deal with multiple results; "all" returns all results,
      "first" returns the first result, "raise" raises an error
    returns:
    - sample_dir_full (str): matching subdirectory
    OR
    - sample_dirs (list[str]): list of all the matching subdirectories
    """
    param_str = (f"{em_clustering_dir}, {sample}, {ref}, {start}, {end},"
            f" {sig}, {inctg}, {dms}")
    sample_dirs = list()
    for sample_dir in os.listdir(em_clustering_dir):
        match = em_dirname_pattern.match(sample_dir)
        if match:
            if (sample != "" and ref != ""
                    and sample_dir.startswith(f"{sample}_{ref}")):
                # If the sample and/or ref name contain underscores, the regex
                # may fail to properly distinguish them. To fix this problem,
                # this code assumes that if sample and ref are given and the
                # directory begins with sample_ref, then the directory is a
                # correct match.
                sample_ref_matched = True
            else:
                sample_ref_matched = False
            valid_match = (
                    (((sample == "" or str(sample) == match.group("sample")) and
                    (ref == "" or str(ref) == match.group("ref"))) or
                    sample_ref_matched) and
                    (start == "" or str(start) == match.group("start")) and
                    (end == "" or str(end) == match.group("end")) and
                    (info == "" or str(info) == match.group("info")) and
                    (sig == "" or str(sig) == match.group("sig")) and
                    (inctg == "" or str(inctg) == match.group("inctg")) and
                    (dms == "" or str(dms) == match.group("dms"))
            )
            if valid_match:
                sample_dir_full = os.path.join(em_clustering_dir, sample_dir)
                if multi == "first":
                    return sample_dir_full
                else:
                    sample_dirs.append(sample_dir_full)
                    if multi == "raise":
                        if len(sample_dirs) > 1:
                            raise ValueError("Multiple runs matched parameters:"
                                    f" {param_str}")
                    elif multi == "all":
                        pass
                    else:
                        raise ValueError(f"Invalid value for multi: {multi}")
    if len(sample_dirs) == 0:
        raise ValueError("No directories matched parameters:"
                f" {param_str}")
    if multi == "raise":
        assert len(sample_dirs) == 1
        return sample_dirs[0]
    elif multi == "all":
        return sample_dirs
    else:
        raise ValueError(f"Invalid value for multi: {multi}")


def get_k_dir(sample_dir, k=""):
    """
    Return the directory of a specified K value from a sample directory in
    EM_Clustering.
    params:
    - sample_dir (str): path to the sample directory in EM_Clustering
    - k (str/int): maximum number of clusters (value of K)
    returns:
    - k_dir (str): path to the run directory
    """
    k = str(k)
    if k.strip() == "":
        k_dirs = [d for d in os.listdir(sample_dir) if d.startswith("K_")]
        if len(k_dirs) == 1:
            k_dir = os.path.join(sample_dir, k_dirs[0])
        else:
            raise ValueError("K must be specified if there are >1 K values.")
    else:
        k_dir = os.path.join(sample_dir, f"K_{k}")
    if not os.path.isdir(k_dir):
        raise ValueError(f"Directory {k_dir} does not exist.")
    return k_dir


def get_run_dir(sample_dir, k, run=""):
    """
    Return the directory of a specified run from a K_i directory in
    EM_Clustering.
    params:
    - sample_dir (str): path to the sample directory in EM_Clustering
    - k (str/int): maximum number of clusters (value of K)
    - run (str/int): either the number of the run or "best" to automatically
      determine which run is the best
    returns:
    - run_dir (str): path to the run directory
    """
    if not run:
        run = ""
    run = str(run)
    k_dir = get_k_dir(sample_dir, k)
    if run.strip() == "":
        run_dirs = [d for d in os.listdir(k_dir) if d.startswith("run_")]
        if len(run_dirs) == 1:
            run_dir = os.path.join(k_dir, run_dirs[0])
        else:
            raise ValueError("Run must be specified if there are >1 runs.")
    elif run == "best":
        run_dir = get_best_run_dir(k_dir)
    else:
        run_dir = os.path.join(k_dir, f"run_{run}")
        if not os.path.isdir(run_dir):
            run_dir += "-best"
    if not os.path.isdir(run_dir):
        raise ValueError(f"Directory {run_dir} does not exist.")
    return run_dir


def get_best_run_dir(k_dir):
    """
    Given an EM_Clustering K directory, return the run labeled "best"
    params:
    - k_directory (str): directory of the K-valued runs
    returns:
    - best_run (str): directory of the best run
    """
    best_runs = [run for run in os.listdir(k_dir) if run.endswith("best")]
    if len(best_runs) == 1:
        best_run = os.path.join(k_dir, best_runs[0])
        return best_run
    elif len(best_runs) == 0:
        raise ValueError(f"No best run in directory {k_dir}")
    else:
        raise ValueError(f"More than one best run in directory {k_dir}")


def get_sample_and_run(em_clustering_dir, k, sample="", ref="",
        start="", end="", info="", sig="", inctg="", dms="", run="best",
        **kwargs):
    """
    A convenience function that first finds the sample directory using
    get_sample_dirs, then determines the directory of the specified run.
    params:
    - all of the parameters for get_sample_dirs and get_best_run, except for
      multi and sample_directory
    - run (str/int): either the number of a specific run or "best" for the best
      run
    returns:
    - run_dir (str): directory of the best run
    """
    sample_dir = get_sample_dirs(em_clustering_dir, sample, ref, start, end,
            info, sig, inctg, dms, multi="raise")
    run_dir = get_run_dir(sample_dir, k, run)
    return run_dir


def get_clusters_mu_filename(**kwargs):
    """
    A convenience function to automatically get the path to a Clusters_Mu.txt
    file.
    params:
    - all parameters for get_sample_and_run
    returns:
    - mu_file (str): path to Clusters_Mu.txt
    """
    run_dir = get_sample_and_run(**kwargs)
    mu_file = os.path.join(run_dir, "Clusters_Mu.txt")
    if not os.path.isfile(mu_file):
        raise FileNotFoundError(mu_file)
    return mu_file


def get_folding_filename(run_dir, cluster, expUp, expDown, ext):
    """
    A convenience function to automatically get the path to a structure file.
    params:
    - all parameters for get_sample_and_run
    returns:
    - f_file (str): path to Clusters_Mu.txt
    """
    path = run_dir.split(os.sep)
    if len(path) < 3:
        raise ValueError(f"Invalid path: {run_dir}")
    if not path[-1].startswith("run_"):
        raise ValueError(f"Invalid path: {run_dir}")
    if path[-2].startswith("K_"):
        k = int(path[-2].split("_")[-1])
    else:
        raise ValueError(f"Invalid path: {run_dir}")
    prefix = path[-3]
    filename = os.path.join(run_dir, f"{prefix}-K{k}_Cluster{cluster}"
            + f"_expUp_{expUp}_expDown_{expDown}{ext}")
    if not os.path.isfile(filename):
        raise FileNotFoundError(filename)
    return filename


def get_cluster_proportions(run_dir):
    """
    Get the proportions of each cluster in a run.
    params:
    - run_dir (str): the directory of the run
    returns:
    - proportions (pd.DataFrame): observed and real proportions of each cluster
    """
    proportions_file = os.path.join(run_dir, "Proportions.txt")
    proportions = pd.read_csv(proportions_file, index_col="Cluster")
    # Convert cluster labels to strings, per the convention in this code.
    proportions.index = list(map(str, proportions.index))
    return proportions

popavg_file_pattern = re.compile("(?P<sample>\S+)_(?P<ref>\S+)_(?P<start>\d+)"
        "_(?P<end>\d+)_popavg_reacts.txt")

def read_many_clusters_mu_files(sample_info, label_delim, norm_func=np.mean):
    """
    Read several Clusters_Mu.txt files and return data in a DataFrame.
    params:
    - sample_info (pd.DataFrame): information for finding Clusters_Mu.txt files.
      See plot_mus for detailed documentation.
    - label_delim (str): delimiter for labels; labels may not contain delimiter
    returns:
    - mus (dict[str/int/float, pd.Series]): mutation rates for each row in
      plots_file
    - pis (dict[str/int/float, float]): proportions of the clusters
    - matches (dict[str/int/float, re.Match]): re.Match object for each
      directory
    """
    mus = dict()
    pis = dict()
    matches = dict()
    missing_data = dict()
    print("Reading Data Files ...")
    for row in tqdm(sample_info.itertuples(index=True)):
        # Determine the matching directory.
        project_dir = os.path.join(row.Projects, row.Project)
        if hasattr(row, "Pop_Avg") and row.Pop_Avg:
            bv_plots_dir = os.path.join(project_dir, "BitVector_Plots")
            file_path = os.path.join(bv_plots_dir, f"{row.Sample}_{row.Reference}_{row.Start}_{row.End}_popavg_reacts.txt")
            if row.IncludeGU:
                sample_mus = read_mutation_rates(file_path,
                        include_gu=row.IncludeGU)
            else:
                # If Gs and Us are excluded, need to also read the sequence.
                seq_file = os.path.join(project_dir, "Ref_Genome",
                        f"{row.Reference}.fasta")
                name, seq = read_fasta(seq_file)
#                print(f"read_mutation_rates('{file_path}', include_gu={row.IncludeGU}, seq='{seq}')")
                sample_mus = read_mutation_rates(file_path,
                        include_gu=row.IncludeGU, seq=seq)
                match = popavg_file_pattern.match(file_path.split("/")[-1])
        else:    
            em_clustering_dir = os.path.join(project_dir, row.EM_Clustering)
            sample_dir = get_sample_dirs(em_clustering_dir, sample=row.Sample,
                    ref=row.Reference, start=row.Start, end=row.End,
                    info=row.InfoThresh, sig=row.SigThresh, inctg=row.IncTG,
                    dms=row.DMSThresh, multi="raise")
            match = em_dirname_pattern.match(sample_dir.split("/")[-1])
            if not match:
                raise ValueError(f"misnamed directory: {sample_dir}")
            match = match.groupdict()
            # Patch in values for the sample and ref if they were given explicitly.
            # This is in case the regex misidentified the sample or ref, which can
            # happend if they contain underscores.
            for field, value in {"sample": row.Sample,
                    "ref": row.Reference}.items():
                if value != "":
                    match[field] = value
            # Determine the directory of the run.
            run_dir = get_run_dir(sample_dir, row.K, row.Run)
            # Determine the path to the Clusters_Mu.txt file.
            clusters_mu_file = os.path.join(run_dir, "Clusters_Mu.txt")
            # Read the mutation rates.
            if row.IncludeGU:
                sample_mus = read_clusters_mu(clusters_mu_file,
                        include_gu=row.IncludeGU)
            else:
                # If Gs and Us are excluded, need to also read the sequence.
                seq_file = os.path.join(project_dir, "Ref_Genome",
                        f"{match['ref']}.fasta")
                name, seq = read_fasta(seq_file)
                sample_mus = read_clusters_mu(clusters_mu_file,
                        include_gu=row.IncludeGU, seq=seq)
            if row.Cluster.strip() == "":
                if len(sample_mus.columns) == 1:
                    # If cluster is not given but there is only one cluster,
                    # fill in the missing value using the only cluster.
                    cluster_label = sample_mus.columns[0]
                    missing_data[row.Index, "Cluster"] = cluster_label
                else:
                    raise ValueError("Cluster must be specified if there are >1"
                            " clusters.")
            else:
                cluster_label = row.Cluster
            if cluster_label == "all":
                cluster_mus = sample_mus
            elif cluster_label in sample_mus.columns:
                cluster_mus = sample_mus[cluster_label]
            else:
                raise ValueError(f"Cluster '{cluster_label}'"
                        f" not in {clusters_mu_file}.")
            # Read the cluster proportion.
            proportions = get_cluster_proportions(run_dir)
            if cluster_label == "all":
                proportion = proportions[" Real pi "].tolist()
            else:
                proportion = proportions.loc[cluster_label, " Real pi "]
        label = str(row.Index)  # Label was assigned to the index in plot_mus.
        if label_delim in label:
            raise ValueError(f"Delimiter '{label_delim}' in label '{label}'.")
        if label in mus:
            raise ValueError(f"Duplicate label: '{label}'")
        else:
            if hasattr(row, "Pop_Avg") and row.Pop_Avg:
                mus[label] = sample_mus
                pis[label] = None
                #Handle matches obj correctly
                matches[label] = {"ref":row.Reference, "sample":row.Sample, "Pop_Avg":True}
            else:
                mus[label] = cluster_mus
                pis[label] = proportion
                match["Pop_Avg"] = False
                matches[label] = match
        if row.NormRef != "":
            if row.NormRef not in sample_info.index:
                raise ValueError("NormRef, if given, must be one of"
                        " the Labels.")
            if row.NormSample == "":
                missing_data[row.Index, "NormSample"] = row.Index
            else:
                if row.NormSample not in sample_info.index:
                    raise ValueError("NormSample, if given, must be one of"
                            " the Labels.")
    # Fill in missing values as needed.
    for (index, column), new_value in missing_data.items():
        sample_info.loc[index, column] = new_value
    # Normalize mus to ratio of median signals in NormSample / NormRef.
    for row in sample_info.itertuples(index=True):
        if row.NormRef != "":
            if isinstance(mus[row.NormRef], pd.Series):
                norm_ref_val = norm_func(mus[row.NormRef])
            else:
                raise ValueError("NormRef can only a single-cluster sample.")
            norm_samp_val = norm_func(mus[row.NormSample], axis=0)
            norm_factor = norm_ref_val / norm_samp_val
            mus[row.Index] = mus[row.Index] * norm_factor
    return mus, pis, matches


def plot_mus(plots_file, label_delim=", "):
    """
    Generate a variety of plots of mutation rates from potentially many samples
    at once.
    params:
    - plots_file (str): path to Excel file containing the information of the
      samples to extract and plots to generate. The file must contain two sheets
      labeled "Files" and "Plots". The "Files" sheet must contain the following
      columns, although not all cells in all columns need to be filled:
      - Label (str/int/float): what to label these mutation rates for later use
      - Projects (str): directory containing all of the projects
      - Project (str): directory containing the project of the current sample
      - EM_Clustering (str): name of the clustering directory
      - Sample (str): name of the sample
      - Reference (str): name of the reference genome
      - Start (int): start position of the bitvectors
      - End (int): end position of the bitvectors
      - InfoThresh (float): info threshold
      - SigThresh (float): signal threshold
      - IncTG (str: "YES"/"NO"): include Ts and Gs during clustering?
        NOTE: not to be confused with parameter IncludeGU
      - DMSThresh (float): DMS threshold
      - K (int): maximum number of clusters
      - Run (int or str: "best"): which clustering run to use
      - Cluster (int): which cluster to use
      - IncludeGU (bool): whether to include Gs and Us in the data output from
        this function. NOTE: not to be confused with IncTG
      - NormData (str): data to which to normalize the DMS signal; must be one
        of the entries in the Label column; if blank, the data in the row are
        not normalized.
      The "Plots" sheet must contain the following columns:
      - Type (str): type of plot; can be "bar", "scatter"
      - Labels (str): labels of the data to include in the plot, as a comma-
        separated list, e.g. "sample1,sample2,sample3"
      - File (str): name of file to save the plot to
    returns: None
    """
    # Get the mutation rate data from the "Files" sheet.
    files_dtypes = {"Label": str, "Projects": str, "Project": str,
            "EM_Clustering": str, "Sample": str, "Reference": str, "Start": str,
            "End": str, "InfoThresh": str, "SigThresh": str, "IncTG": str,
            "DMSThresh": str, "K": str, "Run": str, "Cluster": str,
            "IncludeGU": bool, "NormData": str, "Coronavirus": str,
            "BitVector_Files": str, "Pop_Avg": bool}
    plots_dtypes = {"Type": str, "Labels": str, "File": str,
            "Options": str, "Sample": str, "Length": str,
            "Groups": str, "Window": int, "Threshold": int,
            "Matched_Replicates": bool, "Annotate": str, "Arc": bool, "Ct": str, "Structure": int}
    files = pd.read_excel(plots_file, sheet_name="Files",
            dtype=files_dtypes, index_col="Label", na_filter=False)
    plots = pd.read_excel(plots_file, sheet_name="Plots",
            dtype=plots_dtypes).fillna(value="")
    mus, pis, matches = read_many_clusters_mu_files(files, label_delim)
    # Create a plot for each row in the "Plots" sheet.
    print("Creating Plots ...")
    for row in tqdm(plots.itertuples()):
        labels_value = row.Labels
        if hasattr(row, "Matched_Replicates"):
            matched_replicates = row.Matched_Replicates
        else:
            matched_replicates = False
        if " + " in row.Labels:
            merged = True
            labels_value = row.Labels.replace(" + ", ", ")
        if "; " in labels_value:
            replicate_data = True
            replicates = dict()
            labels = list()
            rep_list = labels_value.split("; ")
            if len(rep_list) > 2:
                raise AttributeError("Currently dreem_utils.py can only handle 2 replicates.")
            for rep_num, replicate in enumerate(rep_list,1):
                replicates[rep_num] = replicate.split(label_delim)
                labels += replicates[rep_num]
        else:
            replicate_data = False
            labels = labels_value.split(label_delim)
        out_file = row.File
        if row.Options.strip() != "":
            options = json.loads(row.Options)
        else:
            options = dict()
        if MATPLOTLIB_RC_PARAM_KEY in options:
            # Set matplotlib parameters if specified.
            for key, value in options[MATPLOTLIB_RC_PARAM_KEY].items():
                matplotlib.rcParams[key] = value
        sub_mus = {l: mus[l] for l in labels}
        sub_mus, groups_dict, single_replicate, merged = parse_groups(sub_mus, row, replicate_data)
        #Collect one file label per group (each group shares a .fasta) for sequence matching.
        file_labels = list()
        for group in groups_dict:
            file_labels.append(groups_dict[group][0])
        labels = list(sub_mus.keys())
        if hasattr(row, "Annotate") and not row.Annotate == '':
            annot_start, annot_end = row.Annotate.split(", ")
            annot = (int(annot_start), int(annot_end))
        else:
            annot = (0,0)

        correlation = ""
        if hasattr(row, "Correlation"):
            if row.Correlation == "spearman":
                correlation = "spearman"
            elif row.Correlation == "pearson":
                correlation = "pearson"

        if hasattr(row, "Threshold") and not row.Threshold == 0:
            threshold = row.Threshold
            for label in sub_mus:
                idxs = filter_coverage(label, threshold, files)
                sub_mus[label] = sub_mus[label][sub_mus[label].index.intersection(idxs)]

        if row.Type == "bar":
            if replicate_data:
                raise AttributeError("bar plots cannot handle replicates.")
            barplot_mus(file_labels, labels, sub_mus, merged, pis, matches, files, out_file, annot,
                    **options)
        elif row.Type == "diffbar":
            if replicate_data:
                raise AttributeError("diffbar plots cannot handle replicates.")
            diffplot_mus(file_labels, labels, sub_mus, merged, pis, matches, files, out_file, annot,
                    **options)
        elif row.Type == "scatter":
            if replicate_data:
                raise AttributeError("scatterplots cannot handle replicates.")
            scatterplot_mus(file_labels, labels, sub_mus, merged, pis, matches, files, out_file, annot,
                    **options)
        elif row.Type == "scatmat":
            if replicate_data:
                raise AttributeError("scatmat plots cannot handle replicates.")
            if merged:
                raise AttributeError("scatmat plots cannot handle merged data.")
            scatterplot_matrix_mus(file_labels, labels, sub_mus, pis, matches, files,
                    out_file, **options)
        elif row.Type == "corrbar":
            groups = None
            if hasattr(row, "Groups"):
                groups = row.Groups.split(label_delim)
                if len(groups) != 2:
                    print("There are not 2 groups, running without user defined group labels.")
            try:
                sample = row.Sample
            except AttributeError:
                raise AttributeError("Corrbar plots require the sample column is populated.")
            corrbar_mus(correlation, sample, file_labels, labels, groups, sub_mus, matched_replicates, pis, matches, files, out_file, **options)
        elif row.Type == "contcorr":
            try:
                sample = row.Sample
            except AttributeError:
                raise AttributeError("Contcorr plots require the sample column is populated.")
            try:
                window = int(row.Window)
            except AttributeError:
                raise AttributeError("Contcorr plots require the 'Window' column is populated.")
            if hasattr(row, "Arc"):
                arc = row.Arc
                if hasattr(row, "Structure"):
                    structure_number = row.Structure
                else:
                    structure_number = 1
                try:
                    ct_file = row.Ct
                except AttributeError:
                    raise AttributeError("Contcorr plots require a .ct file is specified in the 'Ct' column is populated if requesting an arc plot overlay.")
            else:
                arc = False
            contcorr_mus(correlation, sample, labels, sub_mus, replicate_data, matched_replicates, single_replicate, window, arc, ct_file, structure_number, pis, matches, file_labels, files, out_file, annot, **options)
        else:
            raise ValueError(f"Unrecognized type of plot: '{row.Type}'")

def parse_groups(sub_mus, row, replicate_data):
    groups_dict = dict()
    if " + " in row.Labels:
        merged = True
    else:
        merged = False
    if hasattr(row, "Groups"):
        groups = row.Groups.split(", ")
    else:
        if " + " in row.Labels:
            raise AttributeError("Groups column is required for merging.")
        groups = row.Labels.split(label_delim)
    single_replicate = False
    if replicate_data:
        replicate_groups = row.Labels.split("; ")
        if ", " not in row.Labels:
            single_replicate = True
        for rep_num, replicate_group in enumerate(replicate_groups, 1):
            group_labels = replicate_group.split(", ")
            for group_num, group_label in enumerate(group_labels):
                groups_dict[f"{groups[group_num]}_{str(rep_num)}"] = list()
                if " + " in group_label:
                    groups_dict[f"{groups[group_num]}_{str(rep_num)}"] += group_label.split(" + ")
                else:
                    groups_dict[f"{groups[group_num]}_{str(rep_num)}"].append(group_label)
    else:
        group_labels = row.Labels.split(", ")
        if len(groups) < len(group_labels):
            raise AttributeError('Are you trying to plot a single pair replicates? Use a semicolon to separate labels and specify only one group in the "Groups" column. If not, use a comma to separate two groups. These must be specified in the "Groups" column.')
        for group_num, group_label in enumerate(group_labels):
            groups_dict[groups[group_num]] = list()
            if " + " in group_label:
                groups_dict[groups[group_num]] += group_label.split(" + ")
            else:
                groups_dict[groups[group_num]].append(group_label)
    return merge_mus(sub_mus, groups_dict), groups_dict, single_replicate, merged

def merge_mus(sub_mus, merge_groups):
    """
    Align and merge mutation rates from different samples.
    params:
    - sub_mus (dict): Dictionary defining sample labels and mu list e.g. {label1:mu_list1, label2:mu_list2, label3:mu_list3, label4:mu_list4}
    - merge_groups (dict): Dictionary defining groups of lables to be merged e.g. {group1:(label1, label2), group2:(label3, label4)}
    returns: dict
    """
    def merge_group(group_name, label_list, sub_mus):
        group_mus = dict()
        for label in label_list:
            group_mus[label] = sub_mus[label]
        #Merge entire range, missing positions populated with 0
        # index_range = []
        # for mu in group_mus:
        #     for index in group_mus[mu].index.values:
        #         index_range.append(index)
        # min_index = min(index_range)
        # max_index = max(index_range)
        # full_index_range = np.arange(min_index,max_index)
        # first_mu = list(group_mus.keys())[0]
        # group_mus[first_mu] = group_mus[first_mu].reindex(full_index_range)
        dfs = [s.to_frame() for s in group_mus.values()]
        merged_mus = pd.concat(dfs)
        merged_mus = merged_mus.groupby(merged_mus.index).mean()
        merged_mu_series = merged_mus.squeeze(axis=1)
        return merged_mu_series

    final_mus = dict()
    for group in merge_groups:
        final_mus[group] = merge_group(group, merge_groups[group], sub_mus)
    return final_mus

BASE_COLORS = {
        "A": (209/255, 124/255, 111/255),
        "C": (116/255, 145/255, 202/255),
        "G": "orange",
        "T": "green",
        "U": "green"
}

BASE_COLORS = {
        "A": (148/255, 145/255, 199/255),
        "C": (148/255, 145/255, 199/255),
        "G": "orange",
        "T": "green",
        "U": "green"
}

MATPLOTLIB_RC_PARAM_KEY = "mplrc"


def align_mus(mus, matched_indexes=True, ignore_index=False, seqs=None):
    """
    params:
    mus (dict[str, pd.Series]): mutation rates
    """
    if len(mus) > 1:
        if matched_indexes:
            common_indexes = sorted(set.intersection(*[set(mu.index)
                for mu in mus.values()]))
            if len(common_indexes) == 0:
                raise ValueError("Series have no common index positions;"
                        " did you mean to set matched_indexes to False?")
            else:
                mus = {label: m.loc[common_indexes] for label, m in mus.items()}
        else:
            index_lens_match = len({len(m.index)
                    for label, m in mus.items()}) == 1
            if not index_lens_match:
                if seqs is None:
                    raise ValueError("Cannot compare two sets of unequal lengths if"
                        " the indexes are not matched and sequences are not provided.")
                else:
                    if sorted(seqs.keys()) != sorted(mus.keys()):
                        raise ValueError("seqs and mus must have the same keys")
                    seqs_mus = {label:
                            "".join([seqs[label][pos - 1] for pos in mus[label].index])
                            for label in seqs.keys()}
                    print(seqs_mus)
                    shortest_seq_len = min(map(len, seqs_mus.values()))
                    print("len", shortest_seq_len)
                    shortest_seqs = {seq for seq in seqs_mus.values()
                            if len(seq) == shortest_seq_len}
                    if len(shortest_seqs) == 0:
                        raise ValueError("No sequences were given in labels.")
                    elif len(shortest_seqs) == 1:
                        shortest_seq = list(shortest_seqs)[0]
                        seqs_are_nested = all((seq.count(shortest_seq) == 1 for seq in seqs_mus.values()))
                    else:
                        seqs_are_nested = False
                    if not seqs_are_nested:
                        raise ValueError("If indexes are not matched and mus are not all same length,"
                                " all given sequences must be nested.")
                    seq_offsets = {label: seq.index(shortest_seq) for label, seq in seqs_mus.items()}
                    mus = {label: m.iloc[list(range(
                        seq_offsets[label], seq_offsets[label] + shortest_seq_len))]
                        for label, m in mus.items()}
    if ignore_index:
        for m in mus.items():
            m.index = list(range(len(m.index)))
    index_lens_match = len({len(m.index) for label, m in mus.items()}) == 1
    assert index_lens_match
    return mus


def diffplot_mus(file_labels, group_labels, mus, merged, pis, matches, files, out_file, annot,
        base_color=True, title=None, xlabel="Position", ylabel="∆ DMS signal",
        label_titles=False, matched_indexes=True, centered=True, dim_ratio=4,
        **kwargs):
    if len(group_labels) != 2:
        raise ValueError("diffplot_mus requires exactly two values")
    fl1, fl2 = file_labels
    l1, l2 = group_labels
    mus = align_mus(mus, matched_indexes=matched_indexes,
            ignore_index=not matched_indexes)
    mu1, mu2 = mus[l1], mus[l2]
    diff = mu1 - mu2
    if base_color:
        seq_file_1 = os.path.join(files.loc[fl1, "Projects"],
                files.loc[fl1, "Project"], "Ref_Genome",
                f"{matches[fl1]['ref']}.fasta")
        seq_file_2 = os.path.join(files.loc[fl2, "Projects"],
                files.loc[fl2, "Project"], "Ref_Genome",
                f"{matches[fl2]['ref']}.fasta")
        name_1, seq_1 = read_fasta(seq_file_1)
        name_2, seq_2 = read_fasta(seq_file_2)
        colors_1 = [BASE_COLORS[seq_1[pos - 1]] for pos in mu1.index]
        colors_2 = [BASE_COLORS[seq_2[pos - 1]] for pos in mu2.index]
        if colors_1 != colors_2:
            raise ValueError("Cannot color bases if sequences do not match.")
        colors = colors_1
    else:
        colors = None
    fig, ax = plt.subplots()
    ax.bar(diff.index, diff, color=colors)
    axis_title = list()
    for label_num, label in enumerate(group_labels):
        if label_titles:
            axis_title.append(label)
        else:
            row = files.loc[file_labels[label_num], :]
            match = matches[file_labels[label_num]]
            if not match["Pop_Avg"]:
                if not merged:
                    axis_title.append(f"{match['sample']}: K {row['K']}, cluster"
                            f" {row['Cluster']} (p={pis[file_labels[label_num]]}), run {row['Run']}")
                else:
                    axis_title.append(f"{match['sample']}: Merged")
            else:
                axis_title.append(row.Sample)
    axis_title = " - ".join(axis_title)
    ax.set_title(axis_title)
    if title is not None:
        fig.suptitle(title)
    if centered:
        ymin_curr, ymax_curr = ax.get_ylim()
        ylim_new = max(abs(ymin_curr), abs(ymax_curr))
        ax.set_ylim((-ylim_new, ylim_new))
    x_range = ax.get_xlim()[1] - ax.get_xlim()[0]
    y_range = ax.get_ylim()[1] - ax.get_ylim()[0]
    aspect = x_range / y_range / dim_ratio
    ax.set_aspect(aspect)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)

    if annot != (0,0):
        plt.axhline(y = 0, xmin = (annot[0]-min(list(mu1.index)))/(max(list(mu1.index))-min(list(mu1.index))), xmax = (annot[1]-min(list(mu1.index)))/(max(list(mu1.index))-min(list(mu1.index))), color = 'r')

    plt.tight_layout()
    plt.savefig(out_file)
    plt.close()


def scatterplot_mus(file_labels, group_labels, mus, merged, pis, matches, files, out_file, annot,
        base_color=True, title=None, xlabel=None, ylabel=None,
        label_titles=False, matched_indexes=True, margin=0.05,
        xy_line=True, coeff_det=True, pearson=True, spearman=True,
        equal_axes=True, square_plot=True, x_max=None, y_max=None, **kwargs):
    """
    Generate a scatterplot
    params:
    - matched_indexes (bool): If True (default), index positions are assumed to
      correspond (e.g. index 150 in mu1 corresponds to the same base as index
      150 in mu2); if the indexes are not the same length, the intersection is
      taken so that both sets have the same number of points. If False, the
      indexes must have the same length in order to know which points from mu1
      and mu2 correspond to each other.
    """
    if len(group_labels) != 2:
        raise ValueError("scatterplot_mus requires exactly two labels")
    fl1, fl2 = file_labels
    l1, l2 = group_labels
    seq_file_1 = os.path.join(files.loc[fl1, "Projects"],
            files.loc[fl1, "Project"], "Ref_Genome",
            f"{matches[fl1]['ref']}.fasta")
    seq_file_2 = os.path.join(files.loc[fl2, "Projects"],
            files.loc[fl2, "Project"], "Ref_Genome",
            f"{matches[fl2]['ref']}.fasta")
    name_1, seq_1 = read_fasta(seq_file_1)
    name_2, seq_2 = read_fasta(seq_file_2)
    seqs = {fl1: seq_1, fl2: seq_2}
    mus = align_mus(mus, matched_indexes=matched_indexes, seqs=seqs)
    mu1, mu2 = mus[l1], mus[l2]
    mu1 = mu1 / mu1.max()
    mu2 = mu2 / mu2.max()
    print("mu1", mu1.index)
    print("mu2", mu2.index)
    fig, ax = plt.subplots()
    if base_color:
        colors_1 = [BASE_COLORS[seq_1[pos - 1]] for pos in mu1.index]
        colors_2 = [BASE_COLORS[seq_2[pos - 1]] for pos in mu2.index]
        if colors_1 != colors_2:
            raise ValueError("Cannot color bases if sequences do not match.")
        colors = colors_1
    else:
        colors = None
    ax.scatter(mu1, mu2, c=colors)
    if equal_axes:
        if x_max is None and y_max is None:
            max_val = max(mu1.max(), mu2.max())
            xy_lim = max_val * (1 + margin)
            x_max = xy_lim
            y_max = xy_lim
        else:
            if x_max is not None and y_max is not None:
                if not np.isclose(x_max, y_max):
                    raise ValueError("If equal axes and x_max and y_max are given, then x_max must equal y_max")
            elif x_max is not None:
                y_max = x_max
            else:
                x_max = y_max
    else:
        if x_max is None:
            x_max = mu1.max() * (1 + margin)
        if y_max is None:
            y_max = mu2.max() * (1 + margin)
    if square_plot:
        ax.set_aspect(x_max / y_max)
    ax.set_xlim((0, x_max))
    ax.set_ylim((0, y_max))
    corr_text = list()
    if coeff_det:
        pearson_corr, pearson_pval = pearsonr(mu1, mu2)
        corr_text.append(f"R^2 = {round(pearson_corr**2, 5)}"
                f" (logP = {round(np.log10(pearson_pval), 5)})")
    if pearson:
        pearson_corr, pearson_pval = pearsonr(mu1, mu2)
        corr_text.append(f"r = {round(pearson_corr, 5)}"
                f" (logP = {round(np.log10(pearson_pval), 5)})")
    if spearman:
        spearman_corr, spearman_pval = spearmanr(mu1, mu2)
        corr_text.append(f"ρ = {round(spearman_corr, 5)}"
                f" (logP = {round(np.log10(spearman_pval), 5)})")
    if len(corr_text) > 0:
        corr_text = "\n".join(corr_text)
        ax.text(x=x_max*0.01, y=y_max*0.99, s=corr_text,
                horizontalalignment="left", verticalalignment="top")
    if xy_line:
        xy_lim = min(x_max, y_max)
        ax.plot([0, xy_lim], [0, xy_lim], linestyle="dashed", c="gray")
    if xlabel is None:
        if label_titles:
            xlabel = l1
        else:
            if not matches[fl1]["Pop_Avg"]:
                if not merged:
                    xlabel = (f"{matches[fl1]['sample']}: K {files.loc[fl1, 'K']},"
                            f" cluster {files.loc[fl1, 'Cluster']} (p={pis[fl1]}),"
                            f" run {files.loc[fl1, 'Run']}")
                else:
                    xlabel = (f"{matches[fl1]['sample']}: Merged")
            else:
                xlabel = (matches[fl1]['sample'])
    ax.set_xlabel(xlabel)
    if ylabel is None:
        if label_titles:
            ylabel = l2
        else:
            if not matches[fl2]["Pop_Avg"]:
                if not merged:
                    ylabel = (f"{matches[fl2]['sample']}: K {files.loc[fl2, 'K']},"
                            f"cluster {files.loc[fl2, 'Cluster']} (p={pis[fl2]}),"
                            f"run {files.loc[fl2, 'Run']}")
                else:
                    ylabel = (f"{matches[fl2]['sample']}: Merged")
            else:
                ylabel = (matches[fl2]['sample'])
    ax.set_ylabel(ylabel)
    if title is not None:
        ax.set_title(title)

    if annot != (0,0):
        plt.axhline(y = 0, xmin = (annot[0]-min(list(mu1.index)))/(max(list(mu1.index))-min(list(mu1.index))), xmax = (annot[1]-min(list(mu1.index)))/(max(list(mu1.index))-min(list(mu1.index))), color = 'r')

    fig.tight_layout()
    plt.savefig(out_file)
    plt.close()


def scatterplot_matrix_mus(file_labels, group_labels, mus, pis, matches, files, out_file,
        base_color=True, title=None, xlabel=None, ylabel=None,
        label_titles=False, matched_indexes=True, margin=0.05,
        xy_line=True, mu_max_cutoff=0.5,
        marker_size=None, space_per_plot=2.0, space_margin=2.0,
        **kwargs):
    """
    Draw a scatterplot matrix.
    """
    if len(group_labels) != 2:
        raise ValueError("scatterplot_matrix_mus requires exactly two labels")
    fl1, fl2 = file_labels
    l1, l2 = group_labels
    mus = align_mus(mus, matched_indexes=matched_indexes)
    mus1, mus2 = mus[l1], mus[l2]
    if base_color:
        seq_file_1 = os.path.join(files.loc[fl1, "Projects"],
                files.loc[fl1, "Project"], "Ref_Genome",
                f"{matches[fl1]['ref']}.fasta")
        seq_file_2 = os.path.join(files.loc[fl2, "Projects"],
                files.loc[fl2, "Project"], "Ref_Genome",
                f"{matches[fl2]['ref']}.fasta")
        name_1, seq_1 = read_fasta(seq_file_1)
        name_2, seq_2 = read_fasta(seq_file_2)
    else:
        seq_1, seq_2 = None, None
    nrows = len(mus2.columns)
    ncols = len(mus1.columns)
    pearson_corr = np.empty((nrows, ncols))
    pearson_p = np.empty_like(pearson_corr)
    spearman_corr = np.empty((nrows, ncols))
    spearman_p = np.empty_like(spearman_corr)
    fig, axs = plt.subplots(nrows=nrows, ncols=ncols, sharex="all",
            sharey="all", squeeze=False)
    fig.set_size_inches(nrows * space_per_plot + space_margin,
                        ncols * space_per_plot + space_margin)
    if marker_size is None:
        marker_size = space_per_plot
    max_val = max([mus_set.loc[row, col] for mus_set in [mus1, mus2]
            for row, col in itertools.product(mus_set.index, mus_set.columns)
            if mus_set.loc[row, col] <= mu_max_cutoff])
    xy_lim = max_val * (1 + margin)
    for irow, icol in itertools.product(range(nrows), range(ncols)):
        ax = axs[nrows - irow - 1, icol]
        label1 = mus1.columns[icol]  # x label
        label2 = mus2.columns[irow]  # y label
        # Remove points beyond the cutoff.
        mu1 = mus1.loc[:, label1]
        mu2 = mus2.loc[:, label2]
        mu_max_cutoff_filter = np.logical_and(mu1 <= mu_max_cutoff,
                mu2 <= mu_max_cutoff)
        mu1 = mu1.loc[mu_max_cutoff_filter]
        mu2 = mu2.loc[mu_max_cutoff_filter]
        if base_color:
            # Color bases
            colors_1 = [BASE_COLORS[seq_1[pos - 1]] for pos in mu1.index]
            colors_2 = [BASE_COLORS[seq_2[pos - 1]] for pos in mu2.index]
            if colors_1 != colors_2:
                raise ValueError("Cannot color bases if sequences do not match.")
            colors = colors_1
        else:
            colors = None
        ax.scatter(mu1, mu2, c=colors, s=marker_size)
        ax.set_xlim((0, xy_lim))
        ax.set_ylim((0, xy_lim))
        ax.set_aspect("equal")
        if xy_line:
            ax.plot([0, xy_lim], [0, xy_lim], linestyle="dashed", c="gray",
                    linewidth=marker_size)
        if irow == 0:
            ax.set_xlabel(label1)
        if icol == 0:
            ax.set_ylabel(label2)
        pearson_corr[irow, icol], pearson_p[irow, icol] = pearsonr(mu1, mu2)
        spearman_corr[irow, icol], spearman_p[irow, icol] = spearmanr(mu1, mu2)
    if title is not None:
        fig.suptitle(title)
    if xlabel is None:
        if label_titles:
            xlabel = l1
        else:
            if not matches[fl1]["Pop_Avg"]:
                if not merged:
                    xlabel = f"{matches[fl1]['sample']}: K {files.loc[fl1, 'K']}"
                else:
                    xlabel = f"{matches[fl1]['sample']}: Merged"
            else:
                xlabel = f"{matches[fl1]['sample']}: Pop_Avg"
    if ylabel is None:
        if label_titles:
            ylabel = l2
        else:
            if not matches[fl2]["Pop_Avg"]:
                if not merged:
                    ylabel = f"{matches[fl2]['sample']}: K {files.loc[fl2, 'K']}"
                else:
                    ylabel = f"{matches[fl2]['sample']}: Merged"
            else:
                ylabel = f"{matches[fl2]['sample']}: Pop_Avg"
    # add a big axis, hide frame
    fig.add_subplot(111, frameon=False)
    # hide tick and tick label of the big axis
    plt.tick_params(labelcolor="none", top=False, bottom=False, left=False,
            right=False)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    fig.tight_layout()
    plt.savefig(out_file)
    plt.close()
    print()
    print(f"{l1}, {l2}; pearson:")
    print(np.round(pearson_corr, 5))
    print(f"{l1}, {l2}; spearman:")
    print(np.round(spearman_corr, 5))
    return pearson_corr, pearson_p, spearman_corr, spearman_p


def barplot_mus(file_labels, group_labels, mus, merged, pis, matches, files, out_file, annot, sharey=True,
        sharex=False, base_color=True, title=None, xlabel="Position",
        ylabel="DMS signal", label_titles=False, **kwargs):
    """
    Generate a barplot
    """
    n_plots = len(group_labels)
    fig, axs = plt.subplots(n_plots, 1, squeeze=False, sharey=sharey,
            sharex=sharex)
    for i_plot, label in enumerate(file_labels):
        ax = axs[i_plot, 0]
        row = files.loc[label, :]
        mu = mus[group_labels[i_plot]]
        match = matches[label]
        if base_color:
            seq_file = os.path.join(row["Projects"], row["Project"],
                    "Ref_Genome", f"{match['ref']}.fasta")
            name, seq = read_fasta(seq_file)
            colors = [BASE_COLORS[seq[pos - 1]] for pos in mu.index]
        else:
            colors = None
        ax.bar(mu.index, mu, color=colors)

        if annot != (0,0):
            ax.axhline(y = 0, xmin = (annot[0]-min(list(mu.index)))/(max(list(mu.index))-min(list(mu.index))), xmax = (annot[1]-min(list(mu.index)))/(max(list(mu.index))-min(list(mu.index))), color = 'r')
        
        if label_titles:
            axis_title = label
        else:
            if not match['Pop_Avg']:
                if not merged:
                    axis_title = (f"{match['sample']}: K {row['K']}, cluster"
                            f" {row['Cluster']} (p={pis[label]}), run {row['Run']}")
                else:
                    axis_title = (f"{match['sample']}: Merged")
            else:
                axis_title = (f"{match['sample']}: Pop_Avg")
        ax.set_title(axis_title)
    if title is not None:
        fig.suptitle(title)
    if xlabel is not None or ylabel is not None:
        # add a big axis, hide frame
        fig.add_subplot(111, frameon=False)
        # hide tick and tick label of the big axis
        plt.tick_params(labelcolor="none", top=False, bottom=False, left=False,
                right=False)
        if xlabel is not None:
            plt.xlabel(xlabel)
        if ylabel is not None:
            plt.ylabel(ylabel)
    fig.tight_layout()
    plt.savefig(out_file)
    plt.close()


def corrbar_mus(correlation, sample, file_labels, labels, groups, mus, matched_replicates, pis, matches, files, out_file,
        title=None, xlabel=None, ylabel=None,
        label_titles=False, matched_indexes=True, margin=0.05, coeff_det=True, pearson=True, spearman=True, **kwargs):
    """
    Generate a bar chart of correlation between sample replicates and treatment groups. Can accept a "Groups" column in input excel sheet for user defined group names.
    The first two labels (labels separated by ", ") will be assigned to the first group (groups separated by ", ") and the second two labels will be assigned to the second group.
    corrbar_mus will calculate the R^2 value between the following comparisons: group1_label_1 vs group1_label_2, group2_label_1 vs group2_label_2, group1_label_1 vs group2_label_1, group1_label_2 vs group2_label_2.
    If no user defined group names are specified or the "Groups" column is malformed (i.e. it is missing or does not contain 2 groups), the R^2 value will be calculated between the following comparisons: label_1 vs label_2, label_3 vs label_4, label_1 vs label_3, label_2 vs label_4.
    params:
    - matched_indexes (bool): If True (default), index positions are assumed to
      correspond (e.g. index 150 in mu1 corresponds to the same base as index
      150 in mu2); if the indexes are not the same length, the intersection is
      taken so that both sets have the same number of points. If False, the
      indexes must have the same length in order to know which points from mu1
      and mu2 correspond to each other.
    """
    if len(labels) != 4:
        raise ValueError("corrbar_mus requires exactly four labels.")

    fl1, fl3, fl2, fl4 = file_labels
    l1, l3, l2, l4 = labels
    
    if groups is None:
        if matched_replicates:
            groups = [f"{l1}_vs_{l3}", f"{l2}_vs_{l4}", f"{l1}_vs_{l2}", f"{l3}_vs_{l4}"]
        else:
            groups = [f"{l1}_vs_{l3}", f"{l2}_vs_{l4}", f"{l1}_vs_{l2}", f"{l3}_vs_{l4}", f"{l1}_vs_{l4}", f"{l2}_vs_{l3}"]
    else:
        group1, group2 = groups
        if matched_replicates:
            groups = [f"{group1}1_vs_{group1}2", f"{group2}1_vs_{group2}2", f"{group1}1_vs_{group2}1", f"{group1}2_vs_{group2}2"]
        else:
            groups = [f"{group1}1_vs_{group1}2", f"{group2}1_vs_{group2}2", f"{group1}1_vs_{group2}1", f"{group1}2_vs_{group2}2", f"{group1}1_vs_{group2}2", f"{group1}2_vs_{group2}1"]
    
    print(f"Using groups: {', '.join(groups)}")

    grouped_data = dict()
    for group in groups:
        grouped_data[group] = {"corrs":dict()}
    
    if matched_replicates:
        grouped_data[groups[0]]["labels"] = (l1, l2)
        grouped_data[groups[1]]["labels"] = (l3, l4)
        grouped_data[groups[2]]["labels"] = (l1, l3)
        grouped_data[groups[3]]["labels"] = (l2, l4)
    else:
        grouped_data[groups[4]]["labels"] = (l1, l4)
        grouped_data[groups[5]]["labels"] = (l2, l3)

    seq_file_1 = os.path.join(files.loc[fl1, "Projects"],
            files.loc[fl1, "Project"], "Ref_Genome",
            f"{matches[fl1]['ref']}.fasta")
    seq_file_2 = os.path.join(files.loc[fl2, "Projects"],
            files.loc[fl2, "Project"], "Ref_Genome",
            f"{matches[fl2]['ref']}.fasta")
    seq_file_3 = os.path.join(files.loc[fl3, "Projects"],
            files.loc[fl3, "Project"], "Ref_Genome",
            f"{matches[fl3]['ref']}.fasta")
    seq_file_4 = os.path.join(files.loc[fl4, "Projects"],
            files.loc[fl4, "Project"], "Ref_Genome",
            f"{matches[fl4]['ref']}.fasta")

    name_1, seq_1 = read_fasta(seq_file_1)
    name_2, seq_2 = read_fasta(seq_file_2)
    name_3, seq_3 = read_fasta(seq_file_3)
    name_4, seq_4 = read_fasta(seq_file_4)

    seqs = {l1: seq_1, l2: seq_2, l3: seq_3, l4: seq_4}
    mus = align_mus(mus, matched_indexes=matched_indexes, seqs=seqs)
    mu1, mu2, mu3, mu4 = mus[l1], mus[l2], mus[l3], mus[l4]

    fig, ax = plt.subplots()

    if coeff_det:
        if correlation == "spearman":
            corr_set1, pval_set1 = spearmanr(mu1, mu2)
            corr_set2, pval_set2 = spearmanr(mu3, mu4)
            corr_set3, pval_set3 = spearmanr(mu1, mu3)
            corr_set4, pval_set4 = spearmanr(mu2, mu4)
            if not matched_replicates:
                corr_set5, pval_set5 = spearmanr(mu1, mu4)
                corr_set6, pval_set6 = spearmanr(mu2, mu3)
        else:
            corr_set1, pval_set1 = pearsonr(mu1, mu2)
            corr_set2, pval_set2 = pearsonr(mu3, mu4)
            corr_set3, pval_set3 = pearsonr(mu1, mu3)
            corr_set4, pval_set4 = pearsonr(mu2, mu4)
            if not matched_replicates:
                corr_set5, pval_set5 = pearsonr(mu1, mu4)
                corr_set6, pval_set6 = pearsonr(mu2, mu3)
                if correlation == "":
                    corr_set5 = corr_set5**2
                    corr_set6 = corr_set6**2
            if correlation == "":
                corr_set1 = corr_set1**2
                corr_set2 = corr_set2**2
                corr_set3 = corr_set3**2
                corr_set4 = corr_set4**2

        grouped_data[groups[0]]["corr"] = round(corr_set1, 5)
        grouped_data[groups[1]]["corr"] = round(corr_set2, 5)
        grouped_data[groups[2]]["corr"] = round(corr_set3, 5)
        grouped_data[groups[3]]["corr"] = round(corr_set4, 5)
        if not matched_replicates:
            grouped_data[groups[4]]["corr"] = round(corr_set5, 5)
            grouped_data[groups[5]]["corr"] = round(corr_set6, 5)

    correlations = dict()
    for group in grouped_data:
        correlations[group] = grouped_data[group]["corr"]

    def autolabel(rects):
        """Attach a text label above each bar in *rects*, displaying its height."""
        for rect in rects:
            height = rect.get_height()
            ax.annotate('{}'.format(height),
                        xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3),  # 3 points vertical offset
                        textcoords="offset points",
                        ha='center', va='bottom')

    bar = ax.bar(correlations.keys(), correlations.values(), width=0.7, edgecolor="k")
    autolabel(bar)
    plt.xticks(fontsize=10, rotation=30)
    xy_lim = 1.1 * (1 + margin)
    if xlabel is not None:
        ax.set_xlabel(xlabel)
    if ylabel is None:
        if correlation == "spearman":
            ylabel = "Correlation (Spearman's rho)"
        elif correlation == "pearson":
            ylabel = "Correlation (Pearson's r)"
        else:
            ylabel = "Correlation (R^2)"
    ax.set_ylabel(ylabel)
    ax.set_ylim(0,xy_lim)
    if title is not None:
        ax.set_title(title)
    else:
        ax.set_title(f"{sample} DMS Reactivity Correlation")
    fig.tight_layout()
    plt.savefig(out_file)
    plt.close()


def contcorr_mus(correlation, sample, labels, mus, replicate_data, matched_replicates, single_replicate, window, arc, ct_file, structure_number, pis, matches, file_labels, files, out_file, annot,
        base_color=True, title=None, xlabel=None, ylabel=None,
        label_titles=False, matched_indexes=True, margin=0.05,
        xy_line=True, coeff_det=True, pearson=True, spearman=True,
        equal_axes=True, square_plot=True, x_max=None, y_max=None, **kwargs):
    """
    Generate a continuous correlation plot
    params:
    - matched_indexes (bool): If True (default), index positions are assumed to
      correspond (e.g. index 150 in mu1 corresponds to the same base as index
      150 in mu2); if the indexes are not the same length, the intersection is
      taken so that both sets have the same number of points. If False, the
      indexes must have the same length in order to know which points from mu1
      and mu2 correspond to each other.
    """

    def process_mus(mus, labels, file_labels):
        seqs = dict()
        for label, file_label in zip(labels, file_labels):
            seq_file = os.path.join(files.loc[file_label, "Projects"],
            files.loc[file_label, "Project"], "Ref_Genome",
            f"{matches[file_label]['ref']}.fasta")
            name, seq = read_fasta(seq_file)
            seqs[label] = seq
        return align_mus(mus, matched_indexes=matched_indexes, seqs=seqs)
    
    def plot(group, mus):
        data_dict = dict()
        mu1, mu2 = mus
        mu1 = mu1 / mu1.max()
        mu2 = mu2 / mu2.max()
        indices = list(mu1.index)
        start = 0
        for modified_base_num, position in enumerate(mu1):
            end = start+window
            if correlation == "spearman":
                corr, pval = spearmanr(mu1[start:end], mu2[start:end])
            else:
                corr, pval = pearsonr(mu1[start:end], mu2[start:end])
                if correlation != "pearson":
                    corr = corr**2
         #   pearson_corr, pearson_pval = spearmanr(mu1[start:end], mu2[start:end])
            if end >= len(indices):
                end = len(indices)-1
                middle = (indices[end]-indices[start])/2+indices[start]
                data_dict[middle] = corr
                break
            middle = (indices[end]-indices[start])/2+indices[start]
            # if round(middle) == 250:
            #     print(group)
            #     print(mu1[start:end])
            #     print(mu2[start:end])
            data_dict[middle] = corr
            start += 1
        ax.plot(data_dict.keys(), data_dict.values(), label=group)
        # if equal_axes:
        #     if x_max is None and y_max is None:
        #         max_val = max(mu1.max(), mu2.max())
        #         xy_lim = max_val * (1 + margin)
        #         x_max = xy_lim
        #         y_max = xy_lim
        #     else:
        #         if x_max is not None and y_max is not None:
        #             if not np.isclose(x_max, y_max):
        #                 raise ValueError("If equal axes and x_max and y_max are given, then x_max must equal y_max")
        #         elif x_max is not None:
        #             y_max = x_max
        #         else:
        #             x_max = y_max
        # else:
        #     if x_max is None:
        #         x_max = mu1.max() * (1 + margin)
        #     if y_max is None:
        #         y_max = mu2.max() * (1 + margin)

    fig, ax = plt.subplots()
    processed_mus = process_mus(mus, labels, file_labels)
    
    labels = list(processed_mus.keys())

    #Plot all combinations in an unintuitive order
    # combinations = itertools.combinations(processed_mus, 2)
    # for combination in combinations:
    #     print(sorted(combinations))
    #     comparison = f"{combination[0]}_vs_{combination[1]}"
    #     mus = [processed_mus[combination[0]], processed_mus[combination[1]]]
    #     plot(comparison, mus)

    if replicate_data:
        if single_replicate:
            plot(f"{labels[0]}_vs_{labels[1]}", [processed_mus[labels[0]], processed_mus[labels[1]]])
        else:
            plot(f"{labels[0]}_vs_{labels[2]}", [processed_mus[labels[0]], processed_mus[labels[2]]])
            plot(f"{labels[1]}_vs_{labels[3]}", [processed_mus[labels[1]], processed_mus[labels[3]]])
            plot(f"{labels[0]}_vs_{labels[1]}", [processed_mus[labels[0]], processed_mus[labels[1]]])
            plot(f"{labels[2]}_vs_{labels[3]}", [processed_mus[labels[2]], processed_mus[labels[3]]])
            if not matched_replicates:
                plot(f"{labels[0]}_vs_{labels[3]}", [processed_mus[labels[0]], processed_mus[labels[3]]])
                plot(f"{labels[2]}_vs_{labels[1]}", [processed_mus[labels[2]], processed_mus[labels[1]]])
    else:
        plot(f"{labels[0]}_vs_{labels[1]}", [processed_mus[labels[0]], processed_mus[labels[1]]])

    if arc:
        name, pairs, paired, seq = read_ct_file_single(ct_file, multiple=structure_number)
        ax = plot_arc("", seq, pairs, contcorr=True, contcorr_ax=ax)
    else:
        name = None
    plt.legend()
    ax.set_ylim(0, 1.1)
    if xlabel is None:
        xlabel = "Position (bp)"
    ax.set_xlabel(xlabel)
    if ylabel is None:
        if correlation == "spearman":
            ylabel = "Correlation (Spearman's rho)"
        elif correlation == "pearson":
            ylabel = "Correlation (Pearson's r)"
        else:
            ylabel = "Correlation (R^2)"
    ax.set_ylabel(ylabel)
    if title is not None:
        ax.set_title(title)
    else:
        ax.set_title(f"{sample} DMS Reactivity Correlation")

    if annot != (0,0):
        plt.axhline(y = 0, xmin = (annot[0]-min(list(processed_mus[labels[0]].index)))/(max(list(processed_mus[labels[0]].index))-min(list(processed_mus[labels[0]].index))), xmax = (annot[1]-min(list(processed_mus[labels[0]].index)))/(max(list(processed_mus[labels[0]].index))-min(list(processed_mus[labels[0]].index))), color = 'r')

    fig.tight_layout()
    plt.savefig(out_file)
    plt.close()

bv_file_pattern = re.compile("(?P<sample>\S+)_(?P<ref>\S+)_(?P<start>\d+)"
        "_(?P<end>\d+)_bitvectors.txt")


def get_bitvector_file(label, label_info):
    ref = label_info.Reference
    start = label_info.Start
    end = label_info.End
    sample = label_info.Sample
    project_dir = os.path.join(label_info.Projects, label_info.Project)
    bitvector_files_dir = os.path.join(project_dir, label_info.BitVector_Files)

    param_str = (f"{bitvector_files_dir}, {label}, {ref}, {start}, {end}")
    sample_dirs = list()
    sample_dir_full = ""
    for f in os.listdir(bitvector_files_dir):
        match = bv_file_pattern.match(f)
        if match:
            if (sample != "" and ref != ""
                    and f.startswith(f"{sample}_{ref}")):
                # If the sample and/or ref name contain underscores, the regex
                # may fail to properly distinguish them. To fix this problem,
                # this code assumes that if sample and ref are given and the
                # directory begins with sample_ref, then the directory is a
                # correct match.
                sample_ref_matched = True
            else:
                sample_ref_matched = False
            valid_match = (
                    (((sample == "" or str(sample) == match.group("sample")) and
                    (ref == "" or str(ref) == match.group("ref"))) or
                    sample_ref_matched) and
                    (start == "" or str(start) == match.group("start")) and
                    (end == "" or str(end) == match.group("end"))
            )
            if valid_match:
                bitvectors_file = os.path.join(bitvector_files_dir, f)
                return bitvectors_file
    raise FileNotFoundError(f"The sample bit vector file could not be found in {bitvector_files_dir}")


def filter_coverage(label, threshold, files):
    """
    Given a BitVector_Files directory, return the positions covered by at least {threshold} bit vectors.
    params:
    - bitvector_files_dir (str): directory in which to search for samples
    - threshold (int): number of bit vectors a position must have or exceed to pass filtering
    - files (pd.Dataframe): files sheet of the plot_mus.py excel loaded as a pandas dataframe
    returns:
    - passed (list[int]): positions overlapped by >= threshold informative bit vectors
    """
    label_info = files.loc[label]
    start = int(label_info.Start)
    end = int(label_info.End)

    bitvector_file = get_bitvector_file(label, label_info)
    file_df = pd.read_table(bitvector_file, skiprows=2)
    bitvectors_df = file_df.Bit_vector.apply(lambda x: pd.Series(list(x)))
    bitvectors_df.columns = np.arange(start,end+1)
    passed = list()
    for idx in bitvectors_df.columns:
        pileup_by_value = bitvectors_df[idx].value_counts()
        if "?" in pileup_by_value.index:
            pileup_by_value.drop("?", inplace=True)
        if "." in pileup_by_value.index:
            pileup_by_value.drop(".", inplace=True)
        pileup_count = pileup_by_value.sum()
        if pileup_count >= threshold:
            passed.append(idx)
    return passed


def distance_distribution(vectors, metric, degree):
    """
    Compute the sum of distances between every combination of (degree) vectors.
    For degree = 2, this is the distance between every pair of vectors.
    For degree = 3, returns sum of distances between every combination of
      3 vectors (i.e. the perimeter of every possible triangle).
    For degr
    params:
    - vectors (2D-array-like): array of vectors where every row is a vector
      and every column a dimension
    - metric (str): distance metric; see https://docs.scipy.org/doc/scipy/
      reference/generated/scipy.spatial.distance.pdist.html for documentation.
    - degree (int): degree of the distances; 2 for pairwise, 3 for triplet-
      wise, etc.
    returns:
    - dists (np.ndarray): all of the distances as a 1D-array in arbitrary order.
    """
    # Convert vectors to np.ndarray and check dimensions.
    vectors = np.asarray(vectors)
    assert len(vectors.shape) == 2
    n_vectors, n_dims = vectors.shape
    # Compute pairwise distances.
    pdist = distance.squareform(distance.pdist(vectors, metric=metric))
    # Determine degree-wise distances between the points.
    dists = np.array([
        sum((pdist[v1, v2] for v1, v2 in itertools.combinations(v_comb, 2)))
        for v_comb in itertools.combinations(range(n_vectors), degree)
    ])
    return dists

