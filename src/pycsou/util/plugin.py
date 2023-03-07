import warnings
from importlib.metadata import entry_points

import pycsou.util.warning as pycuw


def _load_entry_points(glob, group, names=None):
    r"""
    Load (if any) entry point Pycsou plugins.

    Pycsou accepts contributions in the form of Pycsou-plugins, which can be developed by third-party contributors
    and are not necessarily tested or verified by the team of Pycsou core developers. While we strive to ensure the
    safety and security of our software framework and its plugins, we cannot guarantee their functionality or safety.
    Users should exercise caution when installing and using plugins and assume full responsibility for any damages or
    issues that may arise from their use. The developers of this software framework are not liable for any harm caused
    by the use of third-party plugins.

    """
    eps = tuple(entry_points(group=group))

    try:
        for i, ep in enumerate(eps):
            ep_load = ep.load()
            name = ep.name
            # If plugin can overload, load directly
            if name.startswith("_"):
                if name[1:] in glob:
                    warnings.warn(
                        f"Plugin `{name}` overloaded an existing Pycsou base class/function, use with " f"caution.",
                        pycuw.ContributionWarning,
                    )
                    glob[name[1:]] = ep_load
                    if names is not None:
                        names.append(name[1:])

                else:
                    warnings.warn(
                        f"Attempted to overload a non existing Pycsou base class/function `{name}`."
                        f"Do not use the prefix `_`.",
                        pycuw.ContributionWarning,
                    )

            # Else, check if class/function already exists in Pycsou
            else:
                if name in glob:
                    warnings.warn(
                        f"Attempting to overload Pycsou base class/function `{name}`.\n"
                        + f"Overloading plugins must start with underscore `_`.\n"
                        + f"Defaulting to base class/function `{name}`.\n",
                        pycuw.ContributionWarning,
                    )
                else:
                    glob[name] = ep_load
                    if names is not None:
                        names.append(name)
                    warnings.warn(f"Plugin `{name}` loaded, use with caution.", pycuw.ContributionWarning)
    except:
        pass

    return names
