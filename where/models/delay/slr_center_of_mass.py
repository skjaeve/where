"""Correct for the center of mass of the satellite

Description:
------------

asdf



$Revision: 15011 $
$Date: 2018-05-04 16:19:35 +0200 (Fri, 04 May 2018) $
$LastChangedBy: hjegei $
"""
# External library imports
import numpy as np

# Where imports
from where import apriori
from where.lib import plugins


@plugins.register
def center_of_mass(dset):
    """Calculate center of mass corrections

    Args:
        dset (Dataset):  Model data.

    Returns:
        Numpy array:     Corrections in meters for each observation.
    """
    output = np.zeros(dset.num_obs)
    com = apriori.get("slr_center_of_mass", sat_name=dset.dataset_name)

    for obs, (time, station) in enumerate(dset.values("time", "station")):
        output[obs] = com[station, time]

    return -output
