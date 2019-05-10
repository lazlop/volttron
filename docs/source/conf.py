# -*- coding: utf-8 -*-
#
# Configuration file for the Sphinx documentation builder.
#
# This file does only contain a selection of the most common options. For a
# full list see the documentation:
# http://www.sphinx-doc.org/en/stable/config

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
# import os
# import sys
# sys.path.insert(0, os.path.abspath('.'))

import subprocess
import sys
import os
from glob import glob
from mock import Mock as MagicMock
from recommonmark.parser import CommonMarkParser

from volttron.platform.agent.utils import execute_command


class Mock(MagicMock):
    @classmethod
    def __getattr__(cls, name):
            return Mock()


MOCK_MODULES = ['loadshape', 'numpy', 'sympy', 'xlrd', 'stomp', 'oadr2',
                'pyodbc', 'lxml', 'stomp.listener',
                'sympy.parsing', 'sympy.parsing.sympy_parser', 'pytest']
sys.modules.update((mod_name, Mock()) for mod_name in MOCK_MODULES)


# -- Project information -----------------------------------------------------

project = 'VOLTTRON'
copyright = '2018, The VOLTTRON Community'
author = 'The VOLTTRON Community'

# The short X.Y version
version = '6.0'
# The full version, including alpha/beta/rc tags
release = '6.0-rc1'


# -- General configuration ---------------------------------------------------

# If your documentation needs a minimal Sphinx version, state it here.
#
# needs_sphinx = '1.0'

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.intersphinx',
    'sphinx.ext.todo',
    'sphinx.ext.coverage',
    'sphinx.ext.mathjax',
    'sphinx.ext.ifconfig',
    'sphinx.ext.viewcode',
    'sphinx.ext.githubpages',
    # https://www.sphinx-doc.org/en/master/usage/extensions/autosectionlabel.html
    'sphinx.ext.autosectionlabel',
    # http://www.sphinx-doc.org/en/master/usage/extensions/todo.html
    'sphinx.ext.todo',
]

# prefix sections with the document so that we can cross link
# sections from different pages.
autosectionlabel_prefix_document = True
autosectionlabel_maxdepth = 5

todo_include_todos = True

# Add any paths that contain templates here, relative to this directory.
templates_path = ['_templates']

# The suffix(es) of source filenames.
# You can specify multiple suffix as a list of string:
#
# source_suffix = ['.rst', '.md']
source_suffix = ['.rst', '.md']
source_parsers = {'.md': CommonMarkParser}

# The master toctree document.
master_doc = 'index'

# The language for content autogenerated by Sphinx. Refer to documentation
# for a list of supported languages.
#
# This is also used if you do content translation via gettext catalogs.
# Usually you set "language" from the command line for these cases.
language = None

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path .
exclude_patterns = []

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = 'sphinx'


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = 'sphinx_rtd_theme'

# Theme options are theme-specific and customize the look and feel of a theme
# further.  For a list of options available for each theme, see the
# documentation.
#
# html_theme_options = {}

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ['_static']

# Custom sidebar templates, must be a dictionary that maps document names
# to template names.
#
# The default sidebars (for documents that don't match any pattern) are
# defined by theme itself.  Builtin themes are using these templates by
# default: ``['localtoc.html', 'relations.html', 'sourcelink.html',
# 'searchbox.html']``.
#
# html_sidebars = {}


# -- Options for HTMLHelp output ---------------------------------------------

# Output file base name for HTML help builder.
htmlhelp_basename = 'VOLTTRONdoc'


# -- Options for LaTeX output ------------------------------------------------

latex_elements = {
    # The paper size ('letterpaper' or 'a4paper').
    #
    # 'papersize': 'letterpaper',

    # The font size ('10pt', '11pt' or '12pt').
    #
    # 'pointsize': '10pt',

    # Additional stuff for the LaTeX preamble.
    #
    # 'preamble': '',

    # Latex figure (float) alignment
    #
    # 'figure_align': 'htbp',
}

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title,
#  author, documentclass [howto, manual, or own class]).
latex_documents = [
    (master_doc, 'VOLTTRON.tex', 'VOLTTRON Documentation',
     'The VOLTTRON Community', 'manual'),
]


# -- Options for manual page output ------------------------------------------

# One entry per manual page. List of tuples
# (source start file, name, description, authors, manual section).
man_pages = [
    (master_doc, 'volttron', 'VOLTTRON Documentation',
     [author], 1)
]


# -- Options for Texinfo output ----------------------------------------------

# Grouping the document tree into Texinfo files. List of tuples
# (source start file, target name, title, author,
#  dir menu entry, description, category)
texinfo_documents = [
    (master_doc, 'VOLTTRON', 'VOLTTRON Documentation',
     author, 'VOLTTRON', 'One line description of project.',
     'Miscellaneous'),
]


# -- Extension configuration -------------------------------------------------

# -- Options for intersphinx extension ---------------------------------------

# Example configuration for intersphinx: refer to the Python standard library.
intersphinx_mapping = {'https://docs.python.org/2.7': None}

# -- Options for todo extension ----------------------------------------------

# If true, `todo` and `todoList` produce output, else they produce nothing.
todo_include_todos = True

# -- Custom code generation ----------------------------------------------


# Custom event handlers for Volttron #
def setup(app):
    """
    Registers callback method on sphinx events. callback method used to
    dynamically generate api-docs rst files which are then converted to html
    by readthedocs
    :param app:
    """
    app.connect('builder-inited', generate_apidoc)
#    app.connect('build-finished', clean_apirst)

#
# script_dir = os.path.dirname(os.path.realpath(__file__))
# apidocs_base_dir =os.path.abspath(script_dir + "/apidocs")
#
#


def generate_apidoc(app):
    """
    Generates apidocs for modules under volttron/platform
    and volttron/services/core
    :param app:
    :return:
    """

    volttron_src = os.path.abspath('../volttron')

    if os.environ.get("READTHEDOCS"):
        volttron_src = os.path.abspath('../../volttron')

    # Exclusions must be full paths to directories
    exlusions = [
        os.path.join(volttron_src, 'lint/'),
        os.path.join(volttron_src, 'drivers/')
    ]
    cmd = ["sphinx-apidoc", '-M', '-d 4', '-o', 'source/volttron_api', '--force', volttron_src]

    cmd.extend(exlusions)
    print("The command is: {}".format(cmd))

    execute_command(cmd)

#     print("\n##In run_apidocs##\n")
#     global script_dir, apidocs_base_dir
#
#     os.makedirs(apidocs_base_dir, 0755)
#     file_name = os.path.join(script_dir,"../docs_exclude_list.txt" )
#     services_excludes = []
#     volttron_excludes = ['tests/**/*']
#     examples_excludes = []
#
#     if os.path.exists(file_name):
#         print "file_name {} exists".format(file_name)
#         with open(file_name,'r') as file:
#             for line in file:
#                 print "line is {}".format(line)
#                 if line.startswith('services'):
#                     _add_to_excludes(services_excludes, line)
#                 elif line.startswith('volttron'):
#                     _add_to_excludes(volttron_excludes, line)
#                 elif line.startswith('examples'):
#                     _add_to_excludes(examples_excludes, line)
#     print ("processed exclude list")
#     print ("services {}".format(services_excludes))
#     print ("volttron excludes {}".format(volttron_excludes))
#
#     # generate api-docs for  services/core
#     docs_subdir=os.path.join(apidocs_base_dir, "services")
#     agent_dirs = glob(script_dir+"/../../services/core/*/")
#     run_apidoc(docs_subdir, agent_dirs, services_excludes)
#
#     # generate api-docs for examples
#     docs_subdir = os.path.join(apidocs_base_dir, "examples")
#     agent_dirs =  glob(script_dir + "/../../examples/*/")
#     agent_dirs += glob(script_dir + "/../../examples/MarketAgents/*/")
#     run_apidoc(docs_subdir, agent_dirs, examples_excludes)
#
#     # generate api-docs for platform core and drivers
#     sys.path.insert(0,
#                     os.path.abspath(script_dir + "/../../volttron"))
#     print("Added to sys path***: {}".format(os.path.abspath(script_dir + "/../..")))
#
#     cmd = ["sphinx-apidoc", '--force', '-o',
#            os.path.join(apidocs_base_dir, "volttron"),
#            script_dir + "/../../volttron"]
#     cmd.extend(volttron_excludes)
#     subprocess.check_call(cmd)
#
#
# def _add_to_excludes(application_excludes, line):
#     global script_dir
#     volttron_root = os.path.abspath(os.path.join(script_dir, "../.."))
#     application_excludes.append(os.path.join(volttron_root, line))
#
#
# def run_apidoc(docs_dir, agent_dirs, exclude_list):
#     """
#     Runs sphinx-apidoc on all subdirectories under the given directory.
#     commnad runs with --force and exclude any setup.py file in the subdirectory
#     :param docs_dir: The base directory into with .rst files are generated.
#     :param module_services_path: directory to search for packages to document
#     """
#
#     for agent_dir in agent_dirs:
#         agent_dir = os.path.abspath(agent_dir)
#         agent_dir = agent_dir[:-1] if agent_dir.endswith("/") else agent_dir
#         sys.path.insert(0, agent_dir)
#         print "Added to syspath {}".format(agent_dir)
#         name = os.path.basename(agent_dir)
#         cmd = ["sphinx-apidoc", '--force', '-e', '-o',
#             os.path.join(apidocs_base_dir, "volttron"),
#             script_dir + "/../../volttron"]
#         cmd.extend(exclude_list)
#         print("RuNNING COMMAND:")
#         print(cmd)
#         subprocess.check_call(cmd)
#
#
# def clean_apirst(app, exception):
#     """
#     Deletes folder containing all auto generated .rst files at the end of
#     sphinx build immaterial of the exit state of sphinx build.
#     :param app:
#     :param exception:
#     """
#     global apidocs_base_dir
#     import shutil
#     print("Cleanup: Removing apidocs directory {}".format(apidocs_base_dir))
#     shutil.rmtree(apidocs_base_dir)
