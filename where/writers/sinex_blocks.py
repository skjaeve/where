"""Contruct data blocks for SINEX files from a dataset

Description:
------------

Construct blocks for the the SINEX format version 2.02 described in [1]_.

@todo: print content in blocks for Site Code other than stations
@todo: Point code. What is it? Hardcoded to A
@todo: file comment block
@todo: content in non-VLBI blocks


References:
-----------

.. [1] SINEX Format https://www.iers.org/IERS/EN/Organization/AnalysisCoordinator/SinexFormat/sinex.html

$Revision: 15267 $
$Date: 2018-06-06 01:18:55 +0200 (Wed, 06 Jun 2018) $
$LastChangedBy: hjegei $

"""
# Standard library imports
from datetime import datetime

import numpy as np

# Where imports
import where
from where import apriori
from where.lib import time
from where.lib.unit import unit
from where.lib import util
from where.lib import config
from where.lib import log

_PARAMS = dict(
    site_pos={"x": "STAX", "y": "STAY", "z": "STAZ"},
    src_dir={"ra": "RS_RA", "dec": "RS_DE"},
    eop_nut={"x": "NUT_X", "y": "NUT_Y"},
    eop_pm={"xp": "XPO", "yp": "YPO"},
    eop_pm_rate={"dxp": "XPOR", "dyp": "YPOR"},
    eop_dut1={"dut1": "UT"},
    eop_lod={"lod": "LOD"},
)

_TECH = {"comb": "C", "doris": "D", "slr": "L", "llr": "M", "gnss": "P", "vlbi": "R"}


class SinexBlocks():

    def __init__(self, dset, fid):
        self.dset = dset
        self.fid = fid
        self.tech_prefix = dset.meta["tech"] + "_"
        self._create_state_vector()

    def _create_state_vector(self):
        self.state_vector = list()
        for parameter in self.dset.meta["normal equation"]["names"]:
            partial_type, name = parameter.split("-", 1)
            partial_type = partial_type.replace(self.tech_prefix, "")
            name = name.split("_")
            ident, name = (name[0], name[1]) if len(name) == 2 else ("----", name[0])
            self.state_vector.append({"type": partial_type, "id": ident, "partial": name})
        self.ids = {sta: self.dset.meta[sta].get("site_id", "----") for sta in self.dset.unique("station")}
        self.ids.update({src: "{:0>4}".format(i) for i, src in enumerate(self.dset.unique("source"), start=1)})
        self.ids.update({"----": "----"})

    def header_line(self):
        """Mandatory header line
        """
        now = time.Time(datetime.now(), scale="utc")
        timestamp = now.yydddsssss

        start = self.dset.time.yydddsssss[0]
        end = self.dset.time.yydddsssss[-1]

        num_params = self.dset.meta["statistics"]["number of unknowns"]
        constraint = "2"
        self.fid.write(
            "%=SNX 2.02 NMA {} NMA {} {} R {:>5} {} S E C \n" "".format(timestamp, start, end, num_params, constraint)
        )

    def end_line(self):
        """Mandatory end of file line
        """
        self.fid.write("%ENDSNX\n")

    def file_reference(self):
        """Mandatory block
        """
        self.fid.write("+FILE/REFERENCE\n")
        self.fid.write(" {:<18} {:<60}\n".format("DESCRIPTION", "Norwegian Mapping Authority"))
        self.fid.write(" {:<18} {:<60}\n".format("OUTPUT", "Daily VLBI solution, Normal equations"))
        analyst = util.get_user_info()
        if "email" in analyst:
            self.fid.write(" {:<18} {:<60}\n".format("ANALYST", analyst["email"]))
        for contact in where.__contact__.split(","):
            self.fid.write(" {:<18} {:<60}\n".format("CONTACT", contact.strip()))
        self.fid.write(" {:<18} {:<60}\n".format("SOFTWARE", "Where v{}".format(where.__version__)))
        # self.fid.write(' {:<18} {:<60}\n'.format('HARDWARE', '---'))
        self.fid.write(
            " {:<18} {:<60}\n".format(
                "INPUT", "{} {}".format(self.dset.meta["input"]["type"], self.dset.meta["input"]["file"])
            )
        )
        self.fid.write("-FILE/REFERENCE\n")

    def file_comment(self):
        """Optional block
        """
        self.fid.write("+FILE/COMMENT\n")
        self.fid.write(" Analyst configuration: \n")
        cfg_str = config.tech.as_str(width=79, key_width=30).split("\n")
        for line in cfg_str[1:]:
            self.fid.write(" {:<79}\n".format(line))
        self.fid.write("-FILE/COMMENT\n")

    def input_history(self):
        """Recommended block

        Refers to other SINEX files used to generate this solution
        """
        self.fid.write("+INPUT/HISTORY\n")
        self.fid.write("-INPUT/HISTORY\n")

    def input_files(self):
        """Optional block

        Linked with _input_history.
        """
        self.fid.write("+INPUT/FILES\n")
        self.fid.write("-INPUT/FILES\n")

    def input_acknowledgements(self):
        """Optional block
        """
        self.fid.write("+INPUT/ACKNOWLEDGEMENTS\n")
        self.fid.write(" {:<3} {:<75}\n".format("NMA", "Norwegian Mapping Authority"))
        self.fid.write("-INPUT/ACKNOWLEDGEMENTS\n")

    def nutation_data(self):
        """Mandatory for VLBI
        """
        self.fid.write("+NUTATION/DATA\n")
        self.fid.write(
            " {:<8} {:<70}\n".format("IAU2000A", "SOFA routines. IAU2006/2000A, CIO based, using X,Y series")
        )
        self.fid.write("-NUTATION/DATA\n")

    def precession_data(self):
        """Mandatory for VLBI
        """
        self.fid.write("+PRECESSION/DATA\n")
        self.fid.write(
            " {:<8} {:<70}\n".format("IAU2006", "SOFA routines. IAU2006/2000A, CIO based, using X,Y series")
        )
        self.fid.write("-PRECESSION/DATA\n")

    def source_id(self):
        """Mandatory for VLBI

        Content:
        *CODE IERS des ICRF designation Comments
        """
        icrf = apriori.get("crf", session=self.dset.dataset_name)
        self.fid.write("+SOURCE/ID\n")
        self.fid.write("*Code IERS nam ICRF designator \n")
        for iers_name in self.dset.unique("source"):
            icrf_name = icrf[iers_name].meta["icrf_name"] if "icrf_name" in icrf[iers_name].meta else ""
            self.fid.write(
                " {:0>4} {:<8} {:<16}\n".format(self.ids[iers_name], iers_name.replace("dot", "."), icrf_name)
            )
        self.fid.write("-SOURCE/ID\n")

    def site_id(self):
        """Mandatory block.

        Content:
        *Code PT Domes____ T Station description___ Approx_lon_ Approx_lat_ App_h__
        """
        self.fid.write("+SITE/ID\n")
        self.fid.write("*Code PT Domes____ T Station description___ Approx_lon_ Approx_lat_ App_h__\n")
        for sta in self.dset.unique("station"):
            site_id = self.dset.meta[sta]["site_id"]
            domes = self.dset.meta[sta]["domes"]
            marker = self.dset.meta[sta]["marker"]
            height = self.dset.meta[sta]["height"]
            description = self.dset.meta[sta]["description"][0:22]
            long_deg, long_min, long_sec = unit.rad_to_dms(self.dset.meta[sta]["longitude"])
            lat_deg, lat_min, lat_sec = unit.rad_to_dms(self.dset.meta[sta]["latitude"])

            self.fid.write(
                " {} {:>2} {:5}{:4} {:1} {:<22} {:>3.0f} {:>2.0f} {:4.1f} {:>3.0f} {:>2.0f} {:4.1f} {:7.1f}"
                "\n".format(
                    site_id,
                    "A",
                    domes,
                    marker,
                    _TECH[self.dset.meta["tech"]],
                    description,
                    (long_deg + 360) % 360,
                    long_min,
                    long_sec,
                    lat_deg,
                    lat_min,
                    lat_sec,
                    height,
                )
            )
        self.fid.write("-SITE/ID\n")

    def site_data(self):
        """Optional block
        """
        self.fid.write("+SITE/DATA\n")
        self.fid.write("-SITE/DATA\n")

    def site_receiver(self):
        """Mandatory for GNSS
        """
        self.fid.write("+SITE/RECEIVER\n")
        self.fid.write("-SITE/RECEIVER\n")

    def site_antenna(self):
        """Mandatory for GNSS
        """
        self.fid.write("+SITE/ANTENNA\n")
        self.fid.write("-SITE/ANTENNA\n")

    def site_gps_phase_center(self):
        """Mandatory for GNSS
        """
        self.fid.write("+SITE/GPS_PHASE_CENTER\n")
        self.fid.write("-SITE/GPS_PHASE_CENTER\n")

    def site_gal_phase_center(self):
        """Mandatory for Galileo
        """
        self.fid.write("+SITE/GAL_PHASE_CENTER\n")
        self.fid.write("-SITE/GAL_PHASE_CENTER\n")

    def site_eccentricity(self):
        """Mandatory
        """
        ecc = apriori.get("eccentricity", rundate=self.dset.rundate)
        self.fid.write("+SITE/ECCENTRICITY\n")
        self.fid.write("*Code PT SBIN T Data_Start__ Data_End____ typ Apr --> Benchmark (m)_____\n")
        for sta in self.dset.unique("station"):
            site_id = self.dset.meta[sta]["site_id"]

            if ecc[site_id]["type"] == "XYZ":
                v1, v2, v3 = ecc[site_id]["vector"][0], ecc[site_id]["vector"][1], ecc[site_id]["vector"][2]
            elif ecc[site_id]["type"] == "NEU":
                v1 = ecc[site_id]["vector"][2]
                v2 = ecc[site_id]["vector"][0]
                v3 = ecc[site_id]["vector"][1]
            self.fid.write(
                " {:4} {:>2} {:>4} {:1} {:12} {:12} {:3} {: 8.4f} {: 8.4f} {: 8.4f}\n"
                "".format(
                    site_id,
                    "A",
                    1,
                    _TECH[self.dset.meta["tech"]],
                    self.dset.time.yydddsssss[0],
                    self.dset.time.yydddsssss[-1],
                    ecc[site_id]["type"],
                    v1,
                    v2,
                    v3,
                )
            )
        self.fid.write("-SITE/ECCENTRICITY\n")

    def satellite_id(self):
        """Recommended for GNSS
        """
        self.fid.write("+SATELLITE/ID\n")
        self.fid.write("-SATELLITE/ID\n")

    def satellite_phase_center(self):
        """Mandatory for GNSS if satellite antenna offsets are not estimated
        """
        self.fid.write("+SATELLITE/PHASE_CENTER\n")
        self.fid.write("-SATELLITE/PHASE_CENTER\n")

    def solution_epochs(self):
        """Mandatory
        """
        self.fid.write("+SOLUTION/EPOCHS\n")
        self.fid.write("*Code PT SBIN T Data_start__ Data_end____ Mean_epoch__\n")
        for site_id in self.dset.unique("site_id"):
            self.fid.write(
                " {:4} {:>2} {:>4} {:1} {:12} {:12} {:12}\n"
                "".format(
                    site_id,
                    "A",
                    1,
                    _TECH[self.dset.meta["tech"]],
                    self.dset.time.yydddsssss[0],
                    self.dset.time.yydddsssss[-1],
                    self.dset.time.mean.yydddsssss,
                )
            )
        self.fid.write("-SOLUTION/EPOCHS\n")

    def bias_epochs(self):
        """Mandatory if bias parameters are included
        """
        self.fid.write("+BIAS/EPOCHS\n")
        self.fid.write("-BIAS/EPOCHS\n")

    def solution_statistics(self):
        """Recommended if available
        """
        self.fid.write("+SOLUTION/STATISTICS\n")
        self.fid.write("* Units for WRMS: meter\n")
        self.fid.write(" {:<30} {:>22.15f}\n".format("NUMBER OF OBSERVATIONS", self.dset.num_obs))
        self.fid.write(
            " {:<30} {:>22.15f}\n".format("NUMBER OF UNKNOWNS", self.dset.meta["statistics"]["number of unknowns"])
        )
        self.fid.write(
            " {:<30} {:>22.15f}\n".format(
                "SQUARE SUM OF RESIDUALS (VTPV)", self.dset.meta["statistics"]["square sum of residuals"]
            )
        )
        self.fid.write(
            " {:<30} {:>22.15f}\n".format(
                "NUMBER OF DEGREES OF FREEDOM", self.dset.meta["statistics"]["degrees of freedom"]
            )
        )
        self.fid.write(
            " {:<30} {:>22.15f}\n".format("VARIANCE FACTOR", self.dset.meta["statistics"]["variance factor"])
        )
        self.fid.write(
            " {:<30} {:>22.15f}\n".format(
                "WEIGHTED SQUARE SUM OF O-C", self.dset.meta["statistics"]["weighted square sum of o-c"]
            )
        )
        if self.dset.meta["tech"] == "P":
            self.fid.write(" {:<30} {:>22.15f}\n".format("SAMPLING INTERVAL (SECONDS)", 0))
            self.fid.write(" {:<30} {:>22.15f}\n".format("PHASE MEASUREMENTS SIGMA", 0))
            self.fid.write(" {:<30} {:>22.15f}\n".format("CODE MEASUREMENTS SIGMA", 0))
        self.fid.write("-SOLUTION/STATISTICS\n")

    def solution_estimate(self):
        """Mandatory
        """
        self.fid.write("+SOLUTION/ESTIMATE\n")
        self.fid.write("*Index Type__ CODE PT SOLN Ref_epoch___ Unit S Total__value________ _Std_dev___\n")
        sol_id = 1
        for i, param in enumerate(self.state_vector, start=1):
            point_code = "A" if param["type"] == "site_pos" else "--"
            try:
                param_type = _PARAMS[param["type"]][param["partial"]]
            except KeyError:
                continue

            param_unit = self.dset.meta["normal equation"]["unit"][i - 1]
            value = (self.dset.meta["normal equation"]["solution"][i - 1] + self._get_apriori_value(param, param_unit))
            if self.dset.meta["normal equation"]["covariance"][i - 1][i - 1] < 0:
                log.error(
                    "Negative covariance ({})for {} {}".format(
                        self.dset.meta["normal equation"]["covariance"][i - 1][i - 1],
                        param_type,
                        self.dset.meta["normal equation"]["names"][i - 1],
                    )
                )
            value_sigma = np.sqrt(self.dset.meta["normal equation"]["covariance"][i - 1][i - 1])

            self.fid.write(
                " {:>5} {:6} {:4} {:2} {:>4} {:12} {:4} {:1} {: 20.14e} {:11.5e}\n"
                "".format(
                    i,
                    param_type,
                    self.ids[param["id"]],
                    point_code,
                    sol_id,
                    self.dset.time.mean.yydddsssss,
                    param_unit,
                    2,
                    value,
                    value_sigma,
                )
            )
        self.fid.write("-SOLUTION/ESTIMATE\n")

    def solution_apriori(self):
        """Mandatory
        """
        self.fid.write("+SOLUTION/APRIORI\n")
        self.fid.write("*Index Type__ CODE PT SOLN Ref_epoch___ Unit S Apriori_value________ _Std_dev___\n")
        sol_id = 1
        for i, param in enumerate(self.state_vector, start=1):
            point_code = "A" if param["type"] == "site_pos" else "--"
            try:
                param_type = _PARAMS[param["type"]][param["partial"]]
            except KeyError:
                continue
            param_unit = self.dset.meta["normal equation"]["unit"][i - 1]
            value = self._get_apriori_value(param, param_unit)
            self.fid.write(
                " {:>5} {:6} {:4} {:2} {:>4} {:12} {:4} {:1} {: 20.14e} {:11.5e}\n"
                "".format(
                    i,
                    param_type,
                    self.ids[param["id"]],
                    point_code,
                    sol_id,
                    self.dset.time.mean.yydddsssss,
                    param_unit,
                    2,
                    value,
                    0,
                )
            )
        self.fid.write("-SOLUTION/APRIORI\n")

    def solution_matrix_estimate(self, matrix_type, structure):
        """Mandatory

        Matrix type can be three values:
            CORR - correlations
            COVA - covariance
            INFO - inverse covariance

        Structure can have two values:
            U - upper triangular matrix
            L - lower triangular matrix
        """
        self.fid.write("+SOLUTION/MATRIX_ESTIMATE {:1} {:4}\n".format(structure, matrix_type))
        self.fid.write("-SOLUTION/MATRIX_ESTIMATE {:1} {:4}\n".format(structure, matrix_type))

    def solution_matrix_apriori(self, matrix_type, structure):
        """Mandatory

         Matrix type can be three values:
            CORR - correlations
            COVA - covariance
            INFO - inverse covariance

        Structure can have two values:
            U - upper triangular matrix
            L - lower triangular matrix
        """
        self.fid.write("+SOLUTION/MATRIX_APRIORI {:1} {:4}\n".format(structure, matrix_type))
        self.fid.write("-SOLUTION/MATRIX_APRIORI {:1} {:4}\n".format(structure, matrix_type))

    def solution_normal_equation_vector(self):
        """Mandatory for normal equations
        """
        self.fid.write("+SOLUTION/NORMAL_EQUATION_VECTOR\n")
        sol_id = 1
        constraint = 2  # TODO
        self.fid.write("*Index Type__ CODE PT SOLN _Ref_Epoch__ Unit S Total_value__________\n")
        for i, param in enumerate(self.state_vector, start=1):
            point_code = "A" if param["type"] == "site_pos" else "--"
            try:
                param_type = _PARAMS[param["type"]][param["partial"]]
            except KeyError:
                continue
            param_unit = self.dset.meta["normal equation"]["unit"][i - 1]
            self.fid.write(
                " {:>5} {:6} {:4} {:2} {:>4} {:12} {:4} {:1} {: 20.14e}\n"
                "".format(
                    i,
                    param_type,
                    self.ids[param["id"]],
                    point_code,
                    sol_id,
                    self.dset.time.mean.yydddsssss,
                    param_unit,
                    constraint,
                    self.dset.meta["normal equation"]["vector"][i - 1],
                )
            )
        self.fid.write("-SOLUTION/NORMAL_EQUATION_VECTOR\n")

    def solution_normal_equation_matrix(self, structure):
        """Mandatory for normal equations

        Structure can have two values:
            U - upper triangular matrix
            L - lower triangular matrix

        """
        mat = self.dset.meta["normal equation"]["matrix"]
        self.fid.write("+SOLUTION/NORMAL_EQUATION_MATRIX {:1}\n".format(structure))
        self.fid.write("*Para1 Para2 Para2+0______________ Para2+1______________ Para2+2______________\n")
        for i in range(len(mat)):
            for j in range(i, len(mat), 3):
                if j + 1 == len(mat):
                    self.fid.write(" {:5} {:5} {: 20.14e}\n".format(i + 1, j + 1, mat[i][j]))
                elif j + 2 == len(mat):
                    self.fid.write(" {:5} {:5} {: 20.14e} {: 20.14e}\n".format(i + 1, j + 1, mat[i][j], mat[i][j + 1]))
                else:
                    self.fid.write(
                        " {:5} {:5} {: 20.14e} {: 20.14e} {: 20.14e}\n".format(
                            i + 1, j + 1, mat[i][j], mat[i][j + 1], mat[i][j + 2]
                        )
                    )
        self.fid.write("-SOLUTION/NORMAL_EQUATION_MATRIX {:1}\n".format(structure))

    def _get_apriori_value(self, param, param_unit):
        if param["type"] == "site_pos":
            trf = apriori.get("trf", time=self.dset.time.utc.mean)
            col_idx = "xyz".index(param["partial"])
            return trf[self.ids[param["id"]]].pos.itrs[col_idx]

        if param["type"] == "src_dir":
            icrf = apriori.get("crf", session=self.dset.dataset_name)
            src = icrf[param["id"]]
            if param["partial"] == "ra":
                pos = src.pos.crs[0]
            elif param["partial"] == "dec":
                pos = src.pos.crs[1]
            return pos

        if param["type"] == "eop_nut":
            eop = apriori.get("eop", time=self.dset.time.utc.mean, models=())
            if param["partial"] == "x":
                return eop.convert_to("dx", param_unit)
            elif param["partial"] == "y":
                return eop.convert_to("dy", param_unit)
        if param["type"] == "eop_pm":
            eop = apriori.get("eop", time=self.dset.time.utc.mean, models=())
            if param["partial"] == "xp":
                return eop.convert_to("x", param_unit)
            elif param["partial"] == "yp":
                return eop.convert_to("y", param_unit)
        if param["type"] == "eop_pm_rate":
            eop = apriori.get("eop", time=self.dset.time.utc.mean, models=())
            if param["partial"] == "dxp":
                return eop.convert_to("x_rate", param_unit)
            elif param["partial"] == "dyp":
                return eop.convert_to("y_rate", param_unit)
        if param["type"] == "eop_dut1":
            eop = apriori.get("eop", time=self.dset.time.utc.mean, models=())
            return eop.convert_to("ut1_utc", param_unit)
        if param["type"] == "eop_lod":
            eop = apriori.get("eop", time=self.dset.time.utc.mean, models=())
            return eop.convert_to("lod", param_unit)
        return 0
