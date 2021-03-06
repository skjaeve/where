#!/usr/bin/env python3
"""Where library module including functions for GNSS modeling

Example:
--------

    from where.lib import gnss
    ...

Description:
------------

This module will provide functions for GNSS modeling.


"""

# External library imports
import numpy as np
import matplotlib.pyplot as plt

# WHERE imports
from where import apriori
from where.lib import config
from where.lib import constant
from where.lib import files
from where.lib import log
from where.lib import mathp
from where.lib import rotation
from where.lib.time import TimeDelta


def check_satellite_eclipse(dset):
    """Check if a satellite is an eclipse

    TODO: Check if a better algorithm exists (e.g. based on beta angle).

    Args:
        dset(Dataset):    Model data
    """
    cos_gamma = np.einsum(
        "ij,ij->i", mathp.unit_vector(dset.sat_posvel.itrs_pos), dset.sat_posvel.itrs_pos_sun
    )  # TODO:  dot product -> better solution dot() function in mathp
    h = np.linalg.norm(dset.sat_posvel.itrs_pos, axis=1) * np.sqrt(1.0 - cos_gamma ** 2)

    satellites_in_eclipse = list()
    for satellite in dset.unique("satellite"):
        idx = dset.filter(satellite=satellite)
        satellite_eclipse = np.logical_and(cos_gamma[idx] < 0, h[idx] < constant.a)
        if np.any(satellite_eclipse == True):
            satellites_in_eclipse.append(satellite)

    return satellites_in_eclipse


def findsun(time):
    """Obtains the position vector of the Sun in relation to Earth (in ECEF).

    This routine is a reimplementation of routine findSun() in model.c of gLAB 3.0.0 software.

    Args:
        time(Time):    Time object

    Returns:
        numpy.ndarray:  Sun position vector given in ECEF [m]
    """
    AU = 1.49597870e8
    gstr, slong, sra, sdec = gsdtime_sun(time)

    sun_pos_x = np.cos(np.deg2rad(sdec)) * np.cos(np.deg2rad(sra)) * AU
    sun_pos_y = np.cos(np.deg2rad(sdec)) * np.sin(np.deg2rad(sra)) * AU
    sun_pos_z = np.sin(np.deg2rad(sdec)) * AU
    sun_pos_eci = np.vstack((sun_pos_x, sun_pos_y, sun_pos_z)).T

    # Rotate from inertial to non inertial system (ECI to ECEF)
    sun_pos_ecef = (rotation.R3(np.deg2rad(gstr)) @ sun_pos_eci.T)[:, :, 0]  # remove 1 dimension

    return sun_pos_ecef


def gsdtime_sun(time):
    """Get position of the sun (low-precision)

    This routine is a reimplementation of routine GSDtime_sun() in model.c of gLAB 3.0.0 software.

    Args:
        time(Time):    Time object

    Returns:
        tuple:  with following entries

    =============== =============== ==================================================================================
     Elements        Type            Description
    =============== =============== ==================================================================================
     gstr            numpy.ndarray   GMST0 (to go from ECEF to inertial) [deg]
     slong           numpy.ndarray   Sun longitude [deg]
     sra             numpy.ndarray   Sun right Ascension [deg]
     sdec            numpy.ndarray   Sun declination in [deg]
    =============== =============== ==================================================================================
    """
    jd = time.mjd_int - 15019.5
    frac = time.jd_frac
    vl = np.mod(279.696678 + 0.9856473354 * jd, 360)
    gstr = np.mod(279.690983 + 0.9856473354 * jd + 360 * frac + 180, 360)
    g = np.deg2rad(np.mod(358.475845 + 0.985600267 * jd, 360))

    slong = vl + (1.91946 - 0.004789 * jd / 36525) * np.sin(g) + 0.020094 * np.sin(2 * g)
    obliq = np.deg2rad(23.45229 - 0.0130125 * jd / 36525)

    slp = np.deg2rad(slong - 0.005686)
    sind = np.sin(obliq) * np.sin(slp)
    cosd = np.sqrt(1 - sind * sind)
    sdec = np.rad2deg(np.arctan2(sind, cosd))

    sra = 180 - np.rad2deg(np.arctan2(sind / cosd / np.tan(obliq), -np.cos(slp) / cosd))

    return gstr, slong, sra, sdec


def get_earth_rotation(dset):
    """Get corrections for satellite position and velocity by Earth rotation

    In a Earth-fixed reference system the Earth's rotation has to be applied, which accounts for time effect of Earth
    rotation during the signal propagates from the satellite to the receiver. Eq. 5.11 in :cite:`subirana2013` is used
    for correcting the satellite position and velocity in the Dataset field 'sat_posvel' about the Earth's rotation
    effect.

    Args:
        dset(Dataset):    Model data

    Returns:
        tuple:    with following entries

    =============== =============== ==================================================================================
     Elements        Type            Description
    =============== =============== ==================================================================================
     sat_pos         numpy.ndarray   Satellite position vector corrections in ITRS and [m]
     vel_pos         numpy.ndarray   Satellite velocity corrections in ITRS and [m/s]
    =============== =============== ==================================================================================
    """
    sat_pos = np.zeros((dset.num_obs, 3))
    sat_vel = np.zeros((dset.num_obs, 3))

    flight_time = get_flight_time(dset)
    rotation_angle = flight_time * constant.omega

    for idx in range(0, dset.num_obs):  # TODO: Vectorize
        sat_pos[idx] = (
            rotation.R3(rotation_angle[idx]).dot(dset.sat_posvel.itrs_pos[idx]) - dset.sat_posvel.itrs_pos[idx]
        )
        sat_vel[idx] = (
            rotation.R3(rotation_angle[idx]).dot(dset.sat_posvel.itrs_vel[idx]) - dset.sat_posvel.itrs_vel[idx]
        )

    return sat_pos, sat_vel


def get_code_observation(dset):
    """Get pseudo-range (code) observations depending on given observation types

    The first element of the observation type variable `dset.meta['obstypes'][sys]` is selected as observation for
    single frequency solution. The order of the observation type variable `dset.meta['obstypes'][sys]` depends on
    the priority list given in the configuration file and the given observations.

    The ionospheric-free linear combination is applied for dual frequency solution.

    Args:
        dset:    Dataset

    Returns:
        numpy.ndarray:  Pseudo-range (code) observation choosen depending on priority list and for dual frequency
                        solution given as ionospheric-free linear combination
    """
    freq_type = config.tech.freq_type.str
    code_obs = np.zeros(dset.num_obs)

    if freq_type == "single":

        for sys in dset.unique("system"):
            idx = dset.filter(system=sys)
            obstype = dset.meta["obstypes"][sys][0]
            code_obs = dset[obstype][idx]

    elif freq_type == "dual":
        code_obs, _ = ionosphere_free_combination(dset)
    else:
        log.fatal(
            "Configuration option 'freq_type = {}' is not valid (Note: Triple frequency solution is not " "in use.).",
            freq_type,
        )

    return code_obs


def get_flight_time(dset):
    """Get flight time of GNSS signal between satellite and receiver

    Args:
        dset(Dataset):    Model data

    Return:
        numpy.ndarray:    Flight time of GNSS signal between satellite and receiver in [s]
    """
    from where.models.delay import gnss_range  # Local import to avoid cyclical import

    # Get geometric range between satellite and receiver position
    geometric_range = gnss_range.gnss_range(dset)

    return geometric_range / constant.c


def get_initial_flight_time(dset, sat_clock_corr=None, rel_clock_corr=None):
    r"""Get initial flight time of GNSS signal between satellite and receiver

    In the following it will be described, how the satellite transmission time is determined. The GNSS receiver
    registers the observation time, i.e. when the satellite signal is tracked by the receiver. In addition the
    pseudorange :math:`P_r^s` between the satellite and the receiver is observed by the GNSS receiver. The first guess
    of time of transmission :math:`t^s` can be determined if we subtract from the receiver time :math:`t_r` the time of
    flight of the GNSS signal based on the pseudorange as follows:

    .. math::
          t_0^s  = t_r - \frac{P_r^s}{c}

    with the speed of light :math:`c` and the flight time of the GNSS signal fromt the satellite to the receiver
    :math:`\frac{P_r^s}{c}`, which is determined in this function.

    The time of satellite transmission has to be corrected like:

    .. math::
        \Delta t^s = t_0^s - \Delta t_{sv} - \Delta t_r,

    with the satellite clock correction :math:`\Delta t_{sv}`:

    .. math::
         \Delta t_{sv} = a_0 + a_1 (t_0^s) + a_2 (t_0^s)^2,

    and the relativistic correction due to orbit eccentricity :math:`\Delta t_r`.

    The satellite clock correction and the relativistic eccentricity correction are applied, if this information is
    already available by the routine call.

    Args:
        dset (Dataset):                   Model data.
        sat_clock_corr (numpy.ndarray):   Satellite clock correction
        rel_clock_corr (numpy.ndarray):   Relativistic clock correction due to orbit eccentricity corrections for each
                                          observation
    Return:
       TimeDelta: Flight time of GNSS signal between satellite and receiver
    """
    # Note: It can be that the observation table 'obs' is not given. For example if different orbit solutions are
    #       compared, it is not necessary to read GNSS observation data. In this case the Dataset time entries
    #       are not corrected for time of flight determined based on pseudorange observations. Instead the given
    #       Dataset time entries are directly used.
    flight_time = np.zeros(dset.num_obs)
    if "obs" in dset.tables:
        for sys in dset.unique("system"):

            # Get code observation type defined by given observation and observation type priority list
            # Note: First element of GNSS observation type list should be used.
            obstype = dset.meta["obstypes"][sys][0]
            log.debug(
                "Code observation '{}' for GNSS '{}' is selected for determination of initial flight time.",
                obstype,
                sys,
            )

            idx = dset.filter(system=sys)
            flight_time[idx] = dset[obstype][idx] / constant.c

    if sat_clock_corr is not None:
        flight_time += sat_clock_corr / constant.c

    if rel_clock_corr is not None:
        flight_time += rel_clock_corr / constant.c

    return TimeDelta(flight_time, format="sec")


def get_line_of_sight(dset):
    """Get the Line of Sight vector from receiver to satellite in the ITRS.
    """
    # TODO: Other solution dset.site_pos.convert_gcrs_to_itrs(dset.site_pos.direction)
    return mathp.unit_vector(dset.sat_posvel.itrs_pos - dset.site_pos.itrs)


def get_rinex_file_version(file_key, file_vars):
    """ Get RINEX file version for a given file key

    Args:
        file_key:       File key defined in files.conf file (e.g. given for RINEX navigation or observation file)
        vars:           Variables needed to identify RINEX file based on definition in files.conf file.

    Returns:
        tuple:         with following elements

    ===============  ==================================================================================
     Elements          Description
    ===============  ==================================================================================
     version          RINEX file version
     filepath         RINEX file path
    ===============  ==================================================================================
    """
    file_path = files.path(file_key, file_vars=file_vars)
    with files.open(file_key, file_vars=file_vars, mode="rt") as infile:
        try:
            version = infile.readline().split()[0]
        except IndexError:
            log.fatal(f"Could not find Rinex version in file {file_path}")

    return version, file_path


def gpssec2jd(wwww, sec):
    """
    FUNCTION: gpsSec2jd(wwww,sec)

    PURPOSE:  Conversion from GPS week and second to Julian Date (JD)

    RETURN:   (float) jd_day, jd_frac - Julian Day and fractional part

    INPUT:    (int) wwww, (float) sec - GPS week and second
    """
    SEC_OF_DAY = 86400.0
    JD_1980_01_06 = 2444244  # Julian date of 6-Jan-1980 + 0.5 d

    # .. Determine GPS day
    wd = np.floor((sec + 43200.0) / 3600.0 / 24.0)  # 0.5 d = 43200.0 s

    # .. Determine remainder
    fracSec = sec + 43200.0 - wd * 3600.0 * 24.0

    # .. Conversion GPS week and day to from Julian Date (JD)
    jd_day = wwww * 7.0 + wd + JD_1980_01_06
    jd_frac = fracSec / SEC_OF_DAY

    return jd_day, jd_frac


def jd2gps(jd):
    """
    FUNCTION: jd2gps(jd)

    PURPOSE:  Conversion from Julian Date (JD) to GPS week and day (started 6-Jan-1980).

    RETURN:   (int) wwww, wd, frac - GPS week, GPS day and fractional part / GPS seconds

    INPUT:    (float) jd - Julian Date
    """
    JD_1980_01_06 = 2444244.5  # Julian date of 6-Jan-1980
    if np.any(jd < JD_1980_01_06):
        log.fatal("Julian Day exceeds the GPS time start date of 6-Jan-1980 (JD 2444244.5).")

    # .. Conversion from Julian Date (JD) to GPS week and day
    wwww = np.floor((jd - JD_1980_01_06) / 7)
    wd = np.floor(jd - JD_1980_01_06 - wwww * 7)
    frac = jd - JD_1980_01_06 - wwww * 7 - wd
    gpssec = (frac + wd) * 86400.0

    return wwww, wd, frac, gpssec


def ionosphere_free_combination(dset):
    """Calculate ionosphere-free linear combination of observations.
    Args:
        dset:    Dataset

    Returns:
        tuple:  with following `numpy.ndarray` arrays

    ===============  ============================================================================================
     Elements         Description
    ===============  ============================================================================================
     C_IF             Array with ionosphere-free linear combination of pseudorange observations in [m].
     L_IF             Array with ionosphere-free linear combination of carrier phase observations in [m].
    ===============  ============================================================================================
    """
    C_IF = np.zeros(dset.num_obs)
    L_IF = np.zeros(dset.num_obs)

    for sys in dset.unique("system"):
        idx = dset.filter(system=sys)

        # Get pseudorange and carrier phase observations for the 1st and 2nd frequency
        #
        # NOTE: The GNSS observation types defined in meta variable 'obstypes' has a defined order, which is determined
        #       by the given observation types for each GNSS and the priority list.
        #
        C1 = dset.meta["obstypes"][sys][0]  # Pseudorange observation for 1st frequency
        L1 = dset.meta["obstypes"][sys][1]  # Carrier phase observation for 1st frequency
        C2 = dset.meta["obstypes"][sys][2]  # Pseudorange observation for 2nd frequency
        L2 = dset.meta["obstypes"][sys][3]  # Carrier phase observation for 2nd frequency

        f1 = constant.get("gnss_freq_" + L1[1], source=sys)  # Frequency of 1st band (C1/L1)
        f2 = constant.get("gnss_freq_" + L2[1], source=sys)  # Frequency of 2nd band (C2/L2)

        # Coefficient of ionospheric-free linear combination
        n = f1 ** 2 / (f1 ** 2 - f2 ** 2)
        m = -f2 ** 2 / (f1 ** 2 - f2 ** 2)

        # Generate ionospheric-free linear combination
        C_IF[idx] = n * dset[C1][idx] + m * dset[C2][idx]
        L_IF[idx] = n * dset[L1][idx] + m * dset[L2][idx]

    return C_IF, L_IF


# TODO hjegei: Better solution?
def llh2xyz(lat, lon, h):
    """Conversion of geodetic (geographical) to cartesian to geodetic.

    Reference: "Geocentric Datum of Australia", Technical Manual, Version 2.4, Intergovernmental Committee on Surveying
               and Mapping, 2 December 2014, page 33

    Args:    (float) lat,lon,h  - Geodetic (geographical) coordinates latitude, east longitude in radian and ellipsoidal
                                  height in meter

    Returns:   (float) x,y,z      - Geocentric cartesian coordiantes [m]
    """

    # .. Local variables
    SEMI_MAJOR_AXIS_WGS84 = 6378137.0
    FLATTENING_WGS84 = 1.0 / 298.257223563
    a = SEMI_MAJOR_AXIS_WGS84
    f = FLATTENING_WGS84

    # .. Calculate help parameters
    e2 = (2 - f) * f  # squared eccentricity
    sin2lat = np.sin(lat) * np.sin(lat)
    v = a / np.sqrt(1 - e2 * sin2lat)

    # .. Calculate coordinates
    x = (v + h) * np.cos(lat) * np.cos(lon)
    y = (v + h) * np.cos(lat) * np.sin(lon)
    z = ((1 - e2) * v + h) * np.sin(lat)

    # .. Return geodetic coordinates in [m]
    return x, y, z


def plot_skyplot(dset):
    """Plot skyplot

    Args:
        dset
    """

    cm = plt.get_cmap("gist_rainbow")
    ax = plt.subplot(111, projection="polar")
    ax.set_prop_cycle(
        plt.cycler("color", [cm(1. * i / len(dset.unique("satellite"))) for i in range(len(dset.unique("satellite")))])
    )
    for sat in dset.unique("satellite"):
        idx = dset.filter(satellite=sat)
        azimuth = dset.site_pos.azimuth[idx]
        zenith_distance = np.rad2deg(dset.site_pos.zenith_distance[idx])
        ax.plot(azimuth, zenith_distance, ".", markersize=7, label=sat)
        ax.set_ylim(90)  # set radius of circle to the maximum elevation
        ax.set_theta_zero_location("N")  # sets 0(deg) to North
        ax.set_theta_direction(-1)  # sets plot clockwise
        ax.set_yticks(range(0, 90, 30))  # sets 3 concentric circles
        ax.set_yticklabels(map(str, range(90, 0, -30)))  # reverse labels
        ax.grid("on")
        ax.legend(fontsize=8, loc=1, bbox_to_anchor=(1.25, 1.0))
        ax.set_title("Skyplot", va="bottom")
    plt.show()
