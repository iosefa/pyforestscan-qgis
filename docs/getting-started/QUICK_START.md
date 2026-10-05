# Quick Start

The plugin ZIP does not bundle PyForestScan or its scientific dependencies.
Managed Processing Engine setup is enabled for Windows and Linux x86_64, with
experimental Apple Silicon support, and requires internet access on first use.
See [Compatibility](../COMPATIBILITY.md).

1. In QGIS, choose **Plugins > Manage and Install Plugins > Install from ZIP**
   and select the PyForestScan QGIS ZIP.
2. Open **PyForestScan Mission Control**.
3. If prompted, open **Tools & Setup** and select **Set Up Processing Engine**.
4. On **Process**, choose **LiDAR Folder Selection** or **Polygon Selection**.
5. Choose the LiDAR data.
6. For Polygon Selection, choose the processing-area layer or vector file.
7. Select the forest products and output folder.
8. Select **Prerun Check** and resolve any blocking message.
9. Select **Process LiDAR**.
10. Review the final products, then use **Load into QGIS** or **Open Folder**.

Individual tools are also available under **Processing > Toolbox > PyForestScan**.
They use the same engine; Mission Control only needs to be opened for initial
setup or repair. If setup fails, its error includes the failed installer stage;
technical details are in `~/.local/share/PyForestScan/backend/logs` on Linux
or `~/Library/Application Support/PyForestScan/backend/logs` on macOS.

For Apple Silicon with QGIS 3.44, install the updated ZIP and restart QGIS before
setup. The installer selects a native ARM engine, including when QGIS itself
runs under Rosetta. Dependency resolution and installer routing have been
checked from Linux; an actual Mac install and LiDAR run are still pending.

Repository recognition, coordinate-system alignment, source preparation,
subdivision, scheduling, checkpointing, and final polygon clipping are automatic
unless Mission Control identifies information that only the user can supply.
