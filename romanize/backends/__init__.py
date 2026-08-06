"""Backends that propose a romanization for a native string.

Each is free and offline. Measured on twelve well-known Assam districts:

    IndicXlit (AI4Bharat, neural)   5/12
    Aksharamukha RomanColloquial    2/12
    Aksharamukha ISO / IAST         0/12

None is good enough to ship unreviewed, which is why the lookup table exists. They are
here to fill it cheaply, and to be compared honestly in the audit.
"""
