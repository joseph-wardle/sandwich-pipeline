import hou

# Open Solami on startup
desks = {d.name(): d for d in hou.ui.desktops()}
if "Solami" in desks:
    desks["Solami"].setAsCurrent()
