from importlib import metadata

extensions = [
    'sphinx.ext.autodoc',
]

source_suffix = '.rst'
master_doc = 'index'
project = 'chimera-ai'
copyright = '2026 onwards Chris Withers'
release = metadata.version('chimera-ai')
exclude_trees = ['_build']
pygments_style = 'sphinx'

html_theme = 'furo'
htmlhelp_basename = 'chimera-aidoc'

nitpicky = True
