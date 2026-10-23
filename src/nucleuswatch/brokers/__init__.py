from . import alerce, fink, lasair

ADAPTERS = {
    alerce.NAME: alerce,
    fink.NAME: fink,
    lasair.NAME: lasair,
}

__all__ = ["ADAPTERS"]
