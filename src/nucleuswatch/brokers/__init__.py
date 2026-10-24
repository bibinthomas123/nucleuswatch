from . import alerce, alerce_lsst, antares, fink, fink_lsst, lasair, lasair_lsst

# Adapters per survey. Broker names are the same in both, so results and
# consensus read identically for ZTF and LSST objects.
SURVEYS = {
    "ztf": {alerce.NAME: alerce, fink.NAME: fink, lasair.NAME: lasair, antares.NAME: antares},
    "lsst": {alerce_lsst.NAME: alerce_lsst, fink_lsst.NAME: fink_lsst, lasair_lsst.NAME: lasair_lsst},
}
ADAPTERS = SURVEYS["ztf"]

__all__ = ["ADAPTERS", "SURVEYS"]
