# Empty on purpose. Its presence at the project root makes pytest treat
# this directory as a rootdir and add it to sys.path, so `from app.x
# import y` resolves correctly whether you run `pytest` or
# `python -m pytest` - without this, plain `pytest` (not `python -m
# pytest`) doesn't add the project root to sys.path, and imports of the
# `app` package fail with ModuleNotFoundError.