"""Nowcasting models (Phase 3).

All model classes expose the same interface:

    model = SomeModel(config)
    model.fit(panels, target_quarter)
    result = model.predict(panels, target_quarter)

``result`` is a dict with at least:
    - ``gdp_yoy``: point estimate of quarterly GDP y/y growth
    - ``model``: model name string
    - ``info``: dict of additional diagnostics
"""
