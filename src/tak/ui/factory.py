from tak.ui.uitype import UIType
from tak.ui.console import ConsoleUserInterface
from tak.ui.web import DEFAULT_ENV_PREFIX, DEFAULT_TIP


# @author Daniel McCoy Stephenson
def createUserInterface(
    uiType,
    currentPrompt,
    header=None,
    title="tak",
    tagline="",
    tip=DEFAULT_TIP,
    envPrefix=DEFAULT_ENV_PREFIX,
    host=None,
    port=None,
):
    """Create the requested front-end behind the BaseUserInterface contract.

    title/tagline/tip only matter to the browser front-ends (they are the page
    heading and the hint under it); envPrefix names the <PREFIX>_WEB_HOST and
    <PREFIX>_WEB_PORT variables the server-backed front-end reads when host and
    port are not given outright. Heavy or browser-only front-ends are imported
    lazily inside their branch so the console has no extra dependencies."""
    if uiType == UIType.CONSOLE:
        return ConsoleUserInterface(currentPrompt, header)
    elif uiType == UIType.WEB:
        # Imported lazily so other modes don't start the HTTP machinery.
        from tak.ui.web import WebUserInterface, resolveAddressFromEnvironment

        if host is None or port is None:
            envHost, envPort = resolveAddressFromEnvironment(envPrefix)
            host = envHost if host is None else host
            port = envPort if port is None else port
        return WebUserInterface(
            currentPrompt,
            header,
            title=title,
            tagline=tagline,
            tip=tip,
            envPrefix=envPrefix,
            host=host,
            port=port,
        )
    elif uiType == UIType.PYODIDE:
        # Imported lazily like the others: it reaches for the browser-only
        # `js` module as soon as it is constructed, so nothing outside a
        # Pyodide Worker should pay for importing it.
        from tak.ui.pyodide import PyodideUserInterface

        return PyodideUserInterface(
            currentPrompt, header, title=title, tagline=tagline, tip=tip
        )
    else:
        raise ValueError("Unsupported UI type: %r" % (uiType,))
