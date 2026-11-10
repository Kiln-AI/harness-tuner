"""The run model: the run screen's state, folded from the loop's events.

Pure Python, with no Textual import. `fold.RunModel` takes the stamped
events (`events.Stamped`) and builds one view per section of the run screen
(`state.py`). The words and numbers in the views are final; the widgets lay
them out and color them by their tones.
"""
