"""The Ranch window: edit a shot's render layers and send them to Tractor
without opening Houdini, follow the farm, and clean up old renders.

It shows the SKD Render Network of the shot's render hip, which reads every
department's publish. Run it with `python -m pipe.core.render.network`; see
`__main__` for which Python. Its headless half is
`pipe.dcc.houdini.render_network`. Its Render Manager tab deletes old render
versions of the shots the artist lights (`manager_page`).
"""
