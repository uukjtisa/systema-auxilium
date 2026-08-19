"""
systema/startup — checks that run BEFORE the controller exists.

Everything in this package must import cheaply and depend on almost nothing:
it runs in front of the first window, on a working copy that may be damaged.
`integrity` is pure stdlib on purpose (no PyQt), so it can be tested headlessly
and so a broken UI layer cannot stop the app from diagnosing itself.
"""
