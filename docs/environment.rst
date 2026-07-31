Environment variables
=====================

Chimera reads three variables of its own, and that short list is deliberate:
what a session *is* — which agent, on which goal — lives in the session
archive, not in the environment, because an environment reaches a foreground
process and not a backgrounded one.

.. envvar:: CHIMERA_WORKSPACE

   The absolute path of the workspace. When set, every ``ch`` command uses
   it; when unset, commands walk up from the current directory to the
   nearest ``kind: workspace`` marker.

   You will often run ``ch`` from a checkout that lives *outside* the
   workspace — a branch you have checked out to review, say — where the
   walk-up finds nothing. So set it once in your shell profile and ``ch``
   works from anywhere:

   .. code-block:: zsh

       # ~/.zshrc
       export CHIMERA_WORKSPACE="$HOME/lycia"

   ``ch doctor`` will tell you if it is unset or points somewhere else, and
   prints the exact line to add — see :doc:`health`.

.. envvar:: _CH_COMPLETE

   Set by your shell, not by you: it is how Click asks ``ch`` for tab
   completions. Chimera notices it only to stay silent while completing — a
   stray line of debug output would corrupt the completion. See
   :doc:`completion`.

.. envvar:: _CHIMERA_COMPLETE

   The same thing for the ``chimera`` command, which is an alias for ``ch``.

Variables chimera does not own
------------------------------

``ch`` reads a few more that belong to other tools, and sets none of them
behind your back:

* **Git's.** When you have not set them yourself, chimera adds connection
  timeouts (``GIT_SSH_COMMAND``, ``GIT_HTTP_LOW_SPEED_LIMIT``,
  ``GIT_HTTP_LOW_SPEED_TIME``) so a dead network fails in seconds instead of
  hanging. Set your own and yours win.
* **``SHELL``**, to know which startup file to name when it tells you how to
  install completions.
* **Claude Code's.** Chimera notices when it is running inside a session and
  which session that is. Those variables are the harness's to define, and
  chimera's notes on how far each can be trusted live with the code.
