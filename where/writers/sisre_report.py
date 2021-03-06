"""Write report about a SISRE analysis run (only for one session and not several sessions (stations))

Description:
------------

asdf




"""
# Standard library imports
from collections import namedtuple
from datetime import datetime
import os

# import subprocess

# External library imports
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# WHERE imports
import where
from where import apriori
from where.lib import config
from where.lib import enums
from where.lib import files
from where.lib import gnss
from where.lib import log
from where.lib import plugins
from where.lib import util

FIGURE_DPI = 200
FIGURE_FORMAT = "png"
GNSS_NAME = {"C": "BeiDou", "E": "Galileo", "G": "GPS", "I": "IRNSS", "J": "QZSS", "R": "GLONASS"}

SubplotConfig = namedtuple("SubplotConfig", ["ylabel", "color", "ydata"])
SubplotConfig.__doc__ = """A convenience class for defining a field for subplot configuration

    Args:
        ylabel (str):           Y-axis label
        color (str):            Color of scatter plot
        ydata (numpy.ndarray):  Y-axis data
    """


AxhlineConfig = namedtuple("AxhlineConfig", ["type", "y_value", "color"])
AxhlineConfig.__doc__ = """A convenience class for defining matplotlib axhline configuration

    Args:
        type (str):             Type (e.g. GNSS identifier E or G for Galileo or GPS)
        y_value (float):        Y-value for horizontal line to be plotted in [m]
        color (str):            Color of horizontal line
    """

# Define dictionary with fields to be printed in SISRE report
FIELDS = {
    "sisre": "SISRE",
    "sisre_orb": "orbit-only SISRE",
    "orb_diff_3d": "3D orbit error",
    "clk_diff_with_dt_mean": "satellite clock correction difference $\Delta t$",
    "bias_brdc": "satellite bias of broadcast clocks",
    "bias_precise": "satellite bias of precise clocks",
}


FIELDS_OLD = {
    "sisre": "Global averaged signal-in-space range error (SISRE)",
    "sisre_orb": "Orbit related error of global average SISRE",
    "orb_diff_3d": "3D orbit error based on difference between broadcast and precise orbit",
    "clk_diff_with_dt_mean": "Satellite clock correction difference between broadcast and precise orbit, whereby averaged clock offset in each epoch is applied.",
    "bias_brdc": "Satellite bias of broadcast clocks",
    "bias_precise": "Satellite bias of precise clocks",
}

# TODO: Maybe better to write a SisreReport class.
@plugins.register
def sisre_report(dset):
    """Write SISRE report

    Args:
        dset (Dataset):       A dataset containing the data.
    """
    write_level = config.tech.get("write_level", default="operational").as_enum("write_level")

    # TODO: Better solution?
    if "sampling_rate" not in dset.vars:  # necessary if called for example by where_concatenate.py
        dset.vars["sampling_rate"] = ""

    with files.open(file_key="output_sisre_report", file_vars=dset.vars, mode="wt") as fid:
        _write_title(fid, dset.rundate)
        _write_information(fid)
        _write_config(fid)
        fid.write("\n# Satellite status\n\n")
        # _unhealthy_satellites(fid, dset)
        # _eclipse_satellites(fid, dset)

        # Generate figure directory to save figures generated for SISRE report
        fid.write("\n# SISRE analysis results\n\n")
        figure_dir = files.path("output_sisre_report_figure", file_vars=dset.vars)
        figure_dir.mkdir(parents=True, exist_ok=True)

        _plot_scatter_orbit_and_clock_differences(fid, figure_dir, dset)
        _plot_scatter_sisre(fid, figure_dir, dset)
        _plot_scatter_field(fid, figure_dir, dset, "sisre")
        # _plot_scatter_field(fid, figure_dir, dset, 'sisre', label=False, legend=False)
        _plot_histogram_sisre(fid, figure_dir, dset)
        _satellite_statistics_and_plot(fid, figure_dir, dset)

        fid.write("\n# Analysis of input files\n\n")
        if write_level <= enums.get_value("write_level", "detail"):
            # _plot_scatter_satellite_bias(fid, figure_dir, dset)
            _plot_scatter_field(fid, figure_dir, dset, "bias_brdc")
            _plot_scatter_field(fid, figure_dir, dset, "bias_precise")

    # Generate PDF from Markdown file
    _markdown_to_pdf(dset)


def _eclipse_satellites(fid, dset):
    """Write overview over satellites in eclipse

    Args:
       fid (_io.TextIOWrapper):  File object.
       dset (Dataset):           A dataset containing the data.
    """
    # TODO: time period of eclipting satellites needed
    # Get broadcast orbit
    brdc = apriori.get(
        "orbit",
        rundate=dset.rundate,
        time=dset.time,
        satellite=tuple(dset.satellite),
        system=tuple(dset.system),
        station=dset.dataset_name.upper(),
        apriori_orbit="broadcast",
    )

    fid.write(
        "{:25s} = {sats:47s}\n"
        "".format("Satellites in eclipse", sats=" ".join(gnss.check_satellite_eclipse(brdc.dset)))
    )


def _get_satellite_type(dset, satellite):
    """Get satellite type (e.g. GALILEO-1, GALILEO-2, BLOCK IIF)

    Args:
       dset (Dataset):      A dataset containing the data.
       satellite (str):     Satellite number (e.g. E01, G02, ...)
    """
    idx = dset.filter(satellite=satellite)
    return set(dset.satellite_type[idx]).pop()


def _markdown_to_pdf(dset):
    """Convert markdown SISRE report file to pdf format
    Args:
       dset (Dataset):           A dataset containing the data.
    """

    if config.where.sisre_report.get("markdown_to_pdf", default=False).bool:
        md_path = str(files.path("output_sisre_report", file_vars=dset.vars))
        pdf_path = md_path.replace(".md", ".pdf")
        program = "pandoc"

        # Convert markdown to pdf with pandoc
        pandoc_args = ["-f markdown", "-V classoption:twoside", "-N", "-o " + pdf_path, md_path]

        log.info("Start: {} {}".format(program, " ".join(pandoc_args)))
        status = os.system(f"{program} {' '.join(pandoc_args)}")
        if status != 0:
            log.error("{} failed with error code {} ({})", program, status, " ".join([program] + pandoc_args))

        # TODO: pandoc subprocess call does not work. Why?
        # process = subprocess.run([program] + pandoc_args)
        # if process.returncode:
        #    log.error("{} failed with error code {} ({})", program, process.returncode, " ".join(process.args))


#
# BAR PLOT
#
def _plot_bar_dataframe_columns(fid, figure_dir, df, field, extra_row_names=None, column="rms"):
    """Generate bar plot of given dataframe columns (colored and ordered by satellite type)

    Args:
       fid (_io.TextIOWrapper): File object.
       figure_dir (PosixPath):  Figure directory.
       df (DataFrame):          Dataframe with data to plot.
       field (str):             Dataset field to plot.
       extra_row_names (list):  List of extra rows removed from the dataframe.
       column (str):            Dataframe column to plot.
    """
    fontsize = 12

    if extra_row_names:
        df_reduced = df.drop(extra_row_names)  # Remove extra rows
    else:
        df_reduced = df

    # Assign to each satellite type a color
    colors = dict()
    # TODO: Better handling of color definition?
    # color_def = ['cornflowerblue', 'firebrick', 'violet', 'gold', 'limegreen', 'deepskyblue', 'orangered']
    color_def = [
        "red",
        "tomato",
        "lightsalmon",
        "navy",
        "mediumblue",
        "blue",
        "royalblue",
        "deepskyblue",
        "paleturquoise",
    ]
    # color_def = ['C'+str(idx) for idx in range(0,10)]
    if len(color_def) < len(set(df_reduced.type)):
        log.fatal(f"Not enough colours defined for number of satellite types (#num: {len(set(df_reduced.type))}).")
    for type_ in sorted(set(df_reduced.type)):
        colors.update({type_: color_def.pop()})

    # Generate bar plot
    df_color = df_reduced["type"].apply(lambda x: colors[x])
    fig_width = len(df_reduced.index) / 4 if len(df_reduced.index) > 30 else 6.4
    ax = df_reduced[column].plot(kind="bar", color=df_color, width=0.8, figsize=(fig_width, fig_width / 1.33))
    ax.set_xlabel("Satellite", fontsize=fontsize)
    ax.set_ylabel(f"{field.upper()} {column.upper()} [m]", fontsize=fontsize)

    # Make legend
    satellite_type_patch = [mpatches.Patch(color=v, label=k) for k, v in sorted(colors.items())]
    ax.legend(handles=satellite_type_patch, bbox_to_anchor=(1.04, 1), loc=2, borderaxespad=0., ncol=1)

    plt.tight_layout()
    plt.savefig(figure_dir / f"plot_bar_{field}_{column}.{FIGURE_FORMAT}", dpi=FIGURE_DPI)
    plt.clf()  # clear the current figure

    fid.write(
        f"![{field.upper()} {column.upper()} for all satellites sorted by satellite type]({figure_dir}/plot_bar_{field}_{column}.{FIGURE_FORMAT})\n"
    )
    fid.write("\n\\clearpage\n\n")


#
# BAR STACKED PLOTS
#
def _plot_bar_stacked(
    fid, df_sisre, df_sisre_orb, figure_path, xlabel="", xticks_rotation=None, axhline=None, with_95th_percentile=True
):
    """Generate bar plot of given dataframe columns (colored and ordered by satellite type)

    Args:
       fid (_io.TextIOWrapper):     File object.
       df_sisre (DataFrame):        Dataframe with SISRE results.
       df_sisre_orb (DataFrame):    Dataframe with orbit-only SISRE results.
       figure_path (PosixPath):     Figure path.
       xlabel (str):                X-axis label.
       xticks_rotation (int):       Rotation angle for x-axis ticks.
       axhline (tuple):             Tuple like (type, y_value, color), whereby:
                                         type - GNSS type identifier
                                         y-value - Y-value for horizontal line
                                         color - Color of horizontal line
       with_95th_percentile (bool): Plot SISRE 95th percentile in addition
    """
    fontsize = 12

    # Generate new dataframe with columns 'orbit-only SISRE' and 'clock-only SISRE'
    columns = ["orbit-only SISRE RMS", "clock-only SISRE RMS"]
    data = np.hstack((np.array([df_sisre_orb.rms]).T, (np.array([df_sisre.rms]) - np.array([df_sisre_orb.rms])).T))

    if with_95th_percentile:
        columns = columns + ["95th percentile SISRE"]
        data = np.hstack((data, (np.array([df_sisre.percentile]) - np.array([df_sisre.rms])).T))

    df_merged = pd.DataFrame(data=data, index=df_sisre.index, columns=columns)

    # Generate bar plot
    fig_width = len(df_merged.index) / 4 if len(df_merged.index) > 30 else 6.4
    ax = df_merged.plot(kind="bar", stacked=True, width=0.8, figsize=(fig_width, fig_width / 1.33))
    ax.set_xlabel(xlabel, fontsize=fontsize)
    ax.set_ylabel(f"Accuracy [m]", fontsize=fontsize)
    if axhline is not None:
        for idx in range(0, len(axhline)):
            plt.axhline(y=axhline[idx].y_value, linewidth=2, color=axhline[idx].color)
    # ax.legend(bbox_to_anchor=(1.04, 1), loc=2, borderaxespad=0., ncol=1)
    ax.legend(bbox_to_anchor=(1, 1.15), loc=1, borderaxespad=0., ncol=2)

    if xticks_rotation is not None:
        plt.xticks(rotation=xticks_rotation)
    plt.tight_layout()
    plt.savefig(figure_path, dpi=FIGURE_DPI)
    plt.clf()  # clear the current figure


def _plot_bar_stacked_sisre(fid, field_dfs, extra_row_names, figure_dir):
    """Generate stacked bar plot with SISRE RMS over all satellites (constellation)

    Args:
       fid (_io.TextIOWrapper): File object.
       field_dfs (dict):        Dictionary with SISRE and orbit-only SISRE dataframe.
       extra_row_names (list):  List of extra rows removed from the dataframe.
       figure_dir (PosixPath):  Figure directory.
    """

    figure_path_threshold = figure_dir / f"plot_bar_stacked_sisre_extra_rows_threshold.{FIGURE_FORMAT}"
    figure_path = figure_dir / f"plot_bar_stacked_sisre_extra_rows.{FIGURE_FORMAT}"

    # Define SISRE thresholds
    sisre_threshold = (AxhlineConfig("E", 2, "red"),)  # TODO: Handling of several GNSS

    # Remove satellite rows
    df_sisre_extra = field_dfs["sisre"].drop(set(field_dfs["sisre"].index) - set(extra_row_names))
    df_sisre_orb_extra = field_dfs["sisre_orb"].drop(set(field_dfs["sisre_orb"].index) - set(extra_row_names))

    # Stacked bar plot WITHOUT threshold
    _plot_bar_stacked(fid, df_sisre_extra, df_sisre_orb_extra, figure_path=figure_path, xticks_rotation=20)

    fid.write(
        f"![Blue bars indicate orbit-only SISRE RMS, orange bars clock-only SISRE RMS and green bars 95th percentile SISRE for the used time processing period and over all satellites.]({figure_path})\n\n"
    )

    # Stacked bar plot WITH threshold
    _plot_bar_stacked(
        fid,
        df_sisre_extra,
        df_sisre_orb_extra,
        figure_path=figure_path_threshold,
        xticks_rotation=20,
        axhline=sisre_threshold,
    )

    fid.write(
        f"![Blue bars indicate orbit-only SISRE RMS, orange bars clock-only SISRE RMS and green bars 95th percentile SISRE for the used time processing period and over all satellites. The threshold lines indicate the minimum performance level of the used GNSS.]({figure_path_threshold})\n"
    )
    fid.write("\n\\clearpage\n\n")


def _plot_bar_stacked_sisre_satellites(fid, field_dfs, extra_row_names, figure_dir):
    """Generate stacked bar plot with SISRE RMS for each satellite

    Args:
       fid (_io.TextIOWrapper): File object.
       field_dfs (dict):        Dictionary with SISRE and orbit-only SISRE dataframe.
       extra_row_names (list):  List of extra rows removed from the dataframe.
       figure_dir (PosixPath):  Figure directory.
    """
    figure_path = figure_dir / f"plot_bar_stacked_sisre.{FIGURE_FORMAT}"
    figure_path_threshold = figure_dir / f"plot_bar_stacked_sisre_threshold.{FIGURE_FORMAT}"

    # Define SISRE thresholds
    sisre_threshold = (AxhlineConfig("E", 2, "red"),)  # TODO: Handling of several GNSS

    # Remove extra rows
    df_sisre = field_dfs["sisre"].drop(extra_row_names)
    df_sisre_orb = field_dfs["sisre_orb"].drop(extra_row_names)

    # Stacked bar plot WITHOUT threshold
    _plot_bar_stacked(fid, df_sisre, df_sisre_orb, figure_path=figure_path, xlabel="Satellite")

    fid.write(
        f"![Blue bars indicate orbit-only SISRE RMS, orange bars clock-only SISRE RMS and green bars 95th percentile SISRE for the used time processing period and each satellite.]({figure_path})\n\n"
    )

    # Stacked bar plot WITH threshold
    _plot_bar_stacked(
        fid, df_sisre, df_sisre_orb, figure_path=figure_path_threshold, xlabel="Satellite", axhline=sisre_threshold
    )

    fid.write(
        f"![Blue bars indicate orbit-only SISRE RMS, orange bars clock-only SISRE RMS and green bars 95th percentile SISRE for the used time processing period and each satellite. The threshold lines indicate the minimum performance level of the used GNSS.]({figure_path_threshold})\n"
    )
    fid.write("\n\\clearpage\n\n")


#
# HISTOGRAM PLOTS
#
def _plot_histogram_subplot(data, axis, system):
    """Plot histogram subplots

    Args:
       data (numpy.ndarray):    Data to plot.
       axis (AxesSubplot):      Subplot axes.
       system (str):            GNSS system identifier (e.g. E, G, ...)
    """
    axis.hist(data, normed=True, bins=30)
    axis.set(xlabel="SISRE [m]", ylabel="Frequency")
    axis.set_title(f"{GNSS_NAME[system]}")
    mean = np.mean(data)
    std = np.std(data)
    axis.text(
        0.98,
        0.98,
        f"$mean={mean:5.3f}\ \pm {std:5.3f}$ m",
        horizontalalignment="right",
        verticalalignment="top",
        transform=axis.transAxes,
    )


def _plot_histogram_sisre(fid, figure_dir, dset):
    """Plot histogram based on SISRE dataset field

    Args:
       fid (_io.TextIOWrapper):  File object.
       figure_dir (PosixPath):   Figure directory
       dset (Dataset):           A dataset containing the data.
    """
    import math

    # TODO: How to handle for example 3 subplots?
    nrows = math.ceil(len(dset.unique("system")) / 2)
    ncols = 2 if len(dset.unique("system")) > 1 else 1
    fig, axes = plt.subplots(nrows=nrows, ncols=ncols, sharex=True, sharey=True, squeeze=False)
    for sys, ax in zip(dset.unique("system"), axes.flatten()):
        idx = dset.filter(system=sys)
        _plot_histogram_subplot(dset.sisre[idx], ax, sys)
    plt.savefig(figure_dir / f"plot_histogram_sisre.{FIGURE_FORMAT}", dpi=FIGURE_DPI)
    plt.clf()  # clear the current figure

    fid.write(f"![Histrogram of SISRE results]({figure_dir}/plot_histogram_sisre.{FIGURE_FORMAT})\n")
    fid.write("\n\\clearpage\n\n")


#
# SCATTER PLOTS
#
def _plot_scatter_satellite_bias(fid, figure_dir, dset):
    """Scatter plot of used broadcast and precise satellite bias by determination of SISRE

    Args:
       fid (_io.TextIOWrapper):  File object.
       figure_dir (PosixPath):   Figure directory
       dset (Dataset):           A dataset containing the data.
    """

    for sys in dset.unique("system"):
        idx = dset.filter(system=sys)

        for field, orbit in {"bias_brdc": "Broadcast", "bias_precise": "Precise"}.items():
            if np.sum(dset[field][idx]) != 0:
                figure_path = figure_dir / f"plot_scatter_{field}_{GNSS_NAME[sys].lower()}.{FIGURE_FORMAT}"
                plt.scatter(dset.time.gps.datetime[idx], dset[field][idx])
                plt.ylabel(f"{orbit} satellite bias [m]")
                plt.xlim([min(dset.time.gps.datetime[idx]), max(dset.time.gps.datetime[idx])])
                plt.xlabel("Time [GPS]")
                plt.title(f"{GNSS_NAME[sys]}")
                plt.savefig(figure_path, dpi=FIGURE_DPI)
                plt.clf()  # clear the current figure

                fid.write(
                    f"![Satellite bias applied for {orbit.lower()} satellite clock corrections]({figure_path})\n"
                )
                fid.write("\n\\clearpage\n\n")


def _plot_scatter_field(fid, figure_dir, dset, field, label=True, legend=True):
    """Scatter plot of given field

    Args:
       fid (_io.TextIOWrapper):  File object.
       figure_dir (PosixPath):   Figure directory
       dset (Dataset):           A dataset containing the data.
       field (str):              Dataset field.
    """
    for sys in dset.unique("system"):
        idx = dset.filter(system=sys)
        figure_path = figure_dir / f"plot_scatter_{field.lower()}_{GNSS_NAME[sys].lower()}.{FIGURE_FORMAT}"

        fig = plt.figure(figsize=(7, 5))
        if label == True:
            for sat in dset.unique("satellite", idx=idx):
                idx_sat = dset.filter(system=sys, satellite=sat)
                if np.sum(dset[field][idx_sat]) != 0:
                    plt.scatter(dset.time.gps.datetime[idx_sat], dset[field][idx_sat], label=sat, marker="o", s=10)
        else:
            if np.sum(dset[field][idx]) != 0:
                plt.scatter(dset.time.gps.datetime[idx], dset[field][idx], marker="o", s=10)

        if legend == True:
            plt.legend(bbox_to_anchor=(1.2, 1), ncol=1)
        text = (
            f"mean $= {np.mean(dset[field][idx]):.2f} \pm {np.std(dset[field][idx]):.2f}$ m"
            f"\nrms $= {np.sqrt(np.mean(np.square(dset[field][idx]))):.2f}$ m"
        )
        fig.text(0.83, 0.9, text, horizontalalignment="right", verticalalignment="top", multialignment="left")
        plt.ylabel(f"{field.upper()} [m]")
        plt.xlim([min(dset.time.gps.datetime[idx]), max(dset.time.gps.datetime[idx])])
        plt.xlabel("Time [GPS]")
        plt.title(f"{GNSS_NAME[sys]}")
        fig.autofmt_xdate()  # rotates and right aligns the x labels, and moves the bottom of the axes up to make room for them
        plt.tight_layout()
        plt.savefig(figure_path, dpi=FIGURE_DPI)
        plt.clf()  # clear the current figure

        fid.write(f"![{field.upper()} for {GNSS_NAME[sys]}]({figure_path})\n")
        fid.write("\n\\clearpage\n\n")


#
# SCATTER SUPPLOTS
#
def _plot_scatter_subplots(xdata, subplots, figure_path, xlabel="", title=""):
    """Generate scatter subplot
    Args:
       xdata (numpy.ndarray):       X-axis data to plot.
       subplots (tuple):            Tuple like (ylabel, color, ydata), whereby:
                                        ylabel (str):           Y-axis label
                                        color (str):            Color of scatter plot
                                        ydata (numpy.ndarray):  Y-axis data
       figure_path (PosixPath):     Figure path.
       xlabel (str):                X-axis label.
       title (str):                 Title of subplot.
    """
    marker = "."  # point marker type

    fig, axes = plt.subplots(len(subplots), 1, sharex=True, sharey=True, figsize=(6, 8))
    # fig.set_figheight(8)  # inches
    fig.suptitle(f"{title}", y=1.0)
    for idx, ax in enumerate(axes):
        ax.set(ylabel=subplots[idx].ylabel)
        ax.set_xlim([min(xdata), max(xdata)])  # otherwise time scale of x-axis is not correct -> Why?
        text = f"mean $= {np.mean(subplots[idx].ydata):.2f} \pm {np.std(subplots[idx].ydata):.2f}$ m"
        ax.text(0.98, 0.98, text, horizontalalignment="right", verticalalignment="top", transform=ax.transAxes)
        ax.scatter(xdata, subplots[idx].ydata, marker=marker, color=subplots[idx].color)
        ax.set(xlabel=xlabel)

    fig.autofmt_xdate()  # rotates and right aligns the x labels, and moves the bottom of the
    # axes up to make room for them
    plt.tight_layout()
    plt.savefig(figure_path, dpi=FIGURE_DPI)
    plt.clf()  # clear the current figure


def _plot_scatter_orbit_and_clock_differences(fid, figure_dir, dset):
    """Scatter subplot of orbit and clock differences between broadcast and precise orbit and clock products

    Args:
       fid (_io.TextIOWrapper):  File object.
       figure_dir (PosixPath):   Figure directory
       dset (Dataset):           A dataset containing the data.
    """
    for sys in dset.unique("system"):
        idx = dset.filter(system=sys)
        figure_path = (
            figure_dir / f"plot_scatter_subplot_orbit_clock_differences_{GNSS_NAME[sys].lower()}.{FIGURE_FORMAT}"
        )

        # Define configuration of subplots
        subplots = (
            SubplotConfig("Δalong-track [m]", "paleturquoise", dset.orb_diff.itrs[:, 0][idx]),
            SubplotConfig("Δcross-track [m]", "deepskyblue", dset.orb_diff.itrs[:, 1][idx]),
            SubplotConfig("Δradial [m]", "royalblue", dset.orb_diff.itrs[:, 2][idx]),
            SubplotConfig("Δclock [m]", "tomato", dset.clk_diff_with_dt_mean[idx]),
        )

        _plot_scatter_subplots(
            dset.time.gps.datetime[idx], subplots, figure_path, xlabel="Time [GPS]", title=f"{GNSS_NAME[sys]}"
        )

        fid.write(
            f"![Difference between precise and broadcast orbits given in along-track, cross-track and radial direction and satellite clock corrections for all satellites.]({figure_path})\n"
        )
        fid.write("\n\\clearpage\n\n")


def _plot_scatter_sisre(fid, figure_dir, dset):
    """Scatter subplot of orbit-only SISRE, clock-only SISRE and SISRE

    Args:
       fid (_io.TextIOWrapper):  File object.
       figure_dir (PosixPath):   Figure directory
       dset (Dataset):           A dataset containing the data.
    """

    for sys in dset.unique("system"):
        idx = dset.filter(system=sys)
        figure_path = figure_dir / f"plot_scatter_subplot_sisre_{GNSS_NAME[sys].lower()}.{FIGURE_FORMAT}"

        # Define configuration of subplots
        subplots = (
            SubplotConfig("orbit-only SISRE [m]", "paleturquoise", dset.sisre_orb[idx]),
            SubplotConfig("clock-only SISRE [m]", "deepskyblue", dset.sisre[idx] - dset.sisre_orb[idx]),
            SubplotConfig("SISRE [m]", "royalblue", dset.sisre[idx]),
        )

        _plot_scatter_subplots(
            dset.time.gps.datetime[idx], subplots, figure_path, xlabel="Time [GPS]", title=f"{GNSS_NAME[sys]}"
        )

        fid.write(f"![Orbit-only SISRE, clock-only SISRE and SISRE results for all satellites.]({figure_path})\n")
        fid.write("\n\\clearpage\n\n")


#
# SATELLITE-WISE PLOTS AND STATISTICS
#


def _generate_satellite_index_dataframe(dset):
    """Generate field DataFrames with the satellites as indices and functional values (rms, mean, ...) as columns

    Args:
        dset (Dataset):             Dataset

    Returns:

        tuple:  with following elements

    ==================  ============================================================================================
     Elements            Description
    ==================  ============================================================================================
     field_dfs           Dictionary with field names as keys and dataframe as value. The dataframes have satellite
                         as indices and statistical information as columns (rms, mean, std, min, max, 95th percentile)
     extra_rows_names    List with extra row names like GNSS and satellite type
    ==================  ============================================================================================

    """
    rms = lambda x: np.sqrt(np.mean(np.square(x)))
    percentile = lambda x: np.percentile(x, 95)
    functions = [rms, np.mean, np.std, np.min, np.max, percentile]
    columns = ["type", "rms", "mean", "std", "min", "max", "percentile"]
    field_dfs = dict()

    # Generate field DataFrames with the satellites as indices and functional values (rms, mean, ...) as columns
    #
    # Example:
    #           type       rms      mean       std       min       max  percentile
    # E11  GALILEO-1  0.175557  0.138370  0.108046  0.014441  0.701326    0.259400
    # E12  GALILEO-1  0.366780  0.310270  0.195602  0.039986  0.945892    0.765318
    # E19  GALILEO-1  0.154111  0.141690  0.060615  0.013444  0.284842    0.244182
    for field in FIELDS.keys():
        extra_row_names = list()
        extra_rows = list()
        df_field = pd.DataFrame(columns=columns)

        # Determine functional values for each satellite
        for satellite in sorted(dset.unique("satellite")):
            row = [_get_satellite_type(dset, satellite)]
            for function in functions:
                try:
                    value = dset.apply(function, field, satellite=satellite)
                except:
                    value = float("nan")
                row.append(value)
            df_row = pd.DataFrame([row], columns=columns, index=[satellite])
            df_field = df_field.append(df_row)

        # Sort dataframe after satellite type -> TODO: Better solution for sorting after index?
        df_field["satellite"] = df_field.index
        df_field = df_field.sort_values(by=["type", "satellite"])
        del df_field["satellite"]

        # Determine functional values for each system
        for system in sorted(dset.unique("system")):
            row = [""]  # Append satellite type
            for function in functions:
                try:
                    value = dset.apply(function, field, system=system)
                except:
                    value = float("nan")
                row.append(value)
            system_name = f"__SYSTEM_{system}__"
            extra_row_names.append(system_name)
            extra_rows.append(pd.DataFrame([row], columns=columns, index=[system_name]))

        # Determine functional values for each satellite type
        for type_ in sorted(dset.unique("satellite_type")):
            row = [""]  # Append satellite type
            for function in functions:
                try:
                    value = dset.apply(function, field, satellite_type=type_)
                except:
                    value = float("nan")
                row.append(value)
            type_name = f"__{type_}__"
            extra_row_names.append(type_name)
            extra_rows.append(pd.DataFrame([row], columns=columns, index=[type_name]))

        # Append extra rows
        for row in extra_rows:
            df_field = df_field.append(row)
        df_field = df_field.reindex(
            columns=columns
        )  # TODO: Why is the column order be changed by appending extra rows?

        # Add field Dataframe to field Dataframe dictionary
        field_dfs.update({field: df_field})

    return field_dfs, extra_row_names


def _satellite_statistics_and_plot(fid, figure_dir, dset):
    """Generate statistics and plots for each field and satellite

    Args:
        fid (_io.TextIOWrapper):    File object
        figure_dir (PosixPath):     Figure directory path
        dset (Dataset):             Dataset
    """

    field_dfs, extra_row_names = _generate_satellite_index_dataframe(dset)

    _plot_bar_stacked_sisre(fid, field_dfs, extra_row_names, figure_dir)
    _plot_bar_stacked_sisre_satellites(fid, field_dfs, extra_row_names, figure_dir)

    # Write field Dataframes
    column = "rms"  # Column to plot

    fid.write(f"\n\n#Statistics\n")
    fid.write(
        "In this Section statistics are represented for: \n\n{}".format(
            "".join(["* " + v + "\n" for v in FIELDS.values()])
        )
    )
    for field, df in field_dfs.items():
        fid.write(f"\n\n##Statistic for {FIELDS[field]}\n")
        fid.write("Unit: meter\n")
        _write_dataframe_to_markdown(fid, df, float_format="6.3f")
        fid.write("  ")

        # _plot_bar_dataframe_columns(fid, figure_dir, df, field, extra_row_names, column=column)
        _plot_bar_dataframe_columns(fid, figure_dir, df, field, column=column)


def _unhealthy_satellites(fid, dset):
    """Write overview over unhealthy satellites

    Args:
       fid (_io.TextIOWrapper):  File object.
       figure_dir (PosixPath):   Figure directory.
       dset (Dataset):           A dataset containing the data.
    """
    brdc = apriori.get(
        "orbit",
        rundate=dset.rundate,
        time=dset.time,
        satellite=tuple(dset.satellite),
        system=tuple(dset.system),
        station=dset.dataset_name.upper(),
        apriori_orbit="broadcast",
    )
    fid.write("{:25s} = {sats:60s}\n" "".format("Unhealthy satellites", sats=" ".join(brdc.unhealthy_satellites())))


def _write_config(fid):
    """Print the configuration options for a given technique and model run date

    Args:
       fid (_io.TextIOWrapper):  File object.
    """
    # Print the individual configuration options
    fid.write("#Configuration of SISRE analysis\n__Located at {}__\n".format(", ".join(config.tech.sources)))
    fid.write("```\n")
    fid.write(str(config.tech.as_str(key_width=25, width=70, only_used=True)))
    fid.write("\n```\n")
    fid.write("\\newpage\n")


def _write_dataframe_to_markdown(fid, df, float_format=""):
    """Write Pandas DataFrame to Markdown table

    Args:
        fid (_io.TextIOWrapper):    File object
        df (DataFrame):             Pandas DataFrame
        float_format (str):         Define formatters for float columns
    """
    column_length = [len(c) for c in df.columns]

    # Write header
    if list(df.index):  # Add DataFrame index to header
        num_space = len(max(df.index))
        head_line_1 = "\n| {} ".format(" " * num_space)
        head_line_2 = "|-{}-".format("-" * num_space)
    else:
        header_1 = ""

    fid.write(head_line_1 + "| {} |\n".format(" | ".join(list(df.columns))))
    fid.write(head_line_2 + "|-{}:|\n".format("-|-".join([n * "-" for n in column_length])))

    # Write data
    for index, row in df.iterrows():

        line = "| {idx:s} |".format(idx=index) if index else ""  # Add DataFrame index column

        for _, v in row.items():
            if isinstance(v, float):
                line = line + " {:{fmt}} |".format(v, fmt=float_format)
            else:
                line = line + " {} |".format(v)
        fid.write(line + "\n")
    fid.write("\n")


#
# WRITE DOCUMENT DESCRIPTION
#
def _write_information(fid):
    """Write information about SISRE analysis

    Args:
       fid (_io.TextIOWrapper):  File object.
    """

    fid.write("\\newpage\n")
    fid.write(
        """#SISRE analysis\n

For the SISRE analysis it is common to apply the average contribution over all points of the Earth within the visibility cone of the satellite (Montenbruck el al., 2014), which is called global averaged SISRE. The SISRE analysis in Where is based on the global averaged SISRE:

\\begin{equation}
     \\text{SISRE}^s = \sqrt{(w_r \cdot \Delta r^s - \Delta t^s)^2 + w_{a,c}^2 \cdot (\Delta {a^s}^2 + \Delta {c^s}^2)}
  \\label{eq:sisre}
\\end{equation}

\\begin{equation}
     \\text{SISRE(orb)}^s = \sqrt{w_r^2 \cdot \Delta {r^s}^2 + w_{a,c}^2 \cdot (\Delta {a^s}^2 + \Delta {c^s}^2)}
  \\label{eq:sisre_orb}
\\end{equation}

\\begin{equation}
     \\text{SISRE(clk)}^s = \\text{SISRE}^s - \\text{SISRE(orb)}^s
  \\label{eq:sisre_clk}
\\end{equation}

\\begin{tabular}{lll}
with\\\\
 & $\\text{SISRE}^s$        & Global averaged SISRE for satellite $s$, \\\\
 & $\\text{SISRE(orb)}^s$   & Orbit-only SISRE (SISRE\_ORB) for satellite $s$, \\\\
 & $\\text{SISRE(clk)}^s$   & Clock-only SISRE for satellite $s$, \\\\
 &$\Delta a^s$, $\Delta c^s$, $\Delta r^s$   & Satellite coordinate differences between broadcast\\\\
 &                                           & and precise ephemeris in along-track, cross-track \\\\
 &                                           & and radial for satellite $s$,\\\\
 &$\Delta t^s$              & Satellite clock correction difference related to CoM \\\\
 &                          & of satellite $s$ and corrected for satellite biases and \\\\
 &                          & averaged clock offset in each epoch\\\\
 &                          & (CLK\_DIFF\_SYS),\\\\
 &$w_r$                     & SISRE weight factor for radial errors (see Table 1),\\\\
 &$w_{a,c}$                 & SISRE weight factor for along-track and cross-track \\\\
 &                          & errors (see Table 1).\\\\
\\end{tabular}

It should be noted that we have neglected the uncertainty of precise ephemeris and clocks by the determination of SISRE.

The SISRE analysis is carried out on daily basis. Each daily solution is cleaned for outliers. The outliers are detected and rejected for each day iteratively using a 3-sigma threshold determined for each satellite. After each iteration the SISRE results are again recomputed.

The SISRE report presents also the 3D orbit error (ORB_DIFF_3D), which is caculated as follows:
\\begin{equation}
     \\text{ORB\_DIFF\_3D}^s = \sqrt{(\Delta {r^s}^2 + \Delta {a^s}^2 + \Delta {c^s}^2)}
  \\label{eq:orb_diff_3d}
\\end{equation}

\\begin{table}[!ht]
\\begin{center}
  \\begin{tabular}[c]{lll}
    \\hline
    System       & $w_r$  & $w_{a,c}$ \\\\
    \\hline
    GPS          & $0.980$ & $0.139$ \\\\
    Galileo      & $0.984$ & $0.124$ \\\\
    \\hline
  \\end{tabular}
  \\caption[The global averaged SISRE weight factors for radial ($w_r$) and along-track and cross-track errors ($w_{a,c}$). The weight factors are given for an elevation mask of $5^{\circ}$ based on Table 4 in \cite{montenbruck2018}.]{The global averaged SISRE weight factors for radial ($w_r$) and along-track and cross-track errors ($w_{a,c}$). The weight factors are given for an elevation mask of $5^{\circ}$ based on Table 4 in Montenbruck et al. 2018.}
  \\label{tab:sisre_weight_factors}
\\end{center}
\\end{table}




\\newpage\n """
    )


def _write_title(fid, rundate):
    """Write title page

    Args:
       fid (_io.TextIOWrapper):  File object.
       rundate (date):           Run date.
    """
    title = f"SISRE analysis for day {rundate:%Y-%m-%d}"
    user_info = util.get_user_info()

    fid.write(
        """---
title: {title}
author: Where v{version} [{user}]
date: {nowdate:%Y-%m-%d}
---
""".format(
            # title=title["text"], version=where.__version__, user=config.analysis.user.str, nowdate=datetime.now()
            title=title,
            version=where.__version__,
            user=user_info.get("name", user_info["user"]),
            nowdate=datetime.now(),  # TODO: Better solution?
        )
    )
