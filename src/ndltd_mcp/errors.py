class NdltdError(Exception):
    """Base class. The message is shown to the model/user as-is, so keep it explicit."""


class NdltdNetworkError(NdltdError):
    """Could not reach the site, timed out, or got a non-200 HTTP status."""


class NdltdParseError(NdltdError):
    """Got a response, but it lacks the structure we expect (site changed, or wrong page)."""


class NdltdBlockedError(NdltdError):
    """The site answered with its 'Blank page' (throttling / IP block). Do NOT retry: stop and tell the user."""


class NdltdCaptchaRequired(NdltdError):
    """The site wants the human to solve a captcha. Never solved by the program."""
