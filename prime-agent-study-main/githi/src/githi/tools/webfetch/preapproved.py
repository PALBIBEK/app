"""Preapproved hosts for WebFetch (GET-only exception list).

SECURITY: these domains are ONLY for WebFetch. Sandbox network policy must
NOT inherit this list -- uploads/POSTs to some of these (huggingface, nuget)
would be an exfil channel.
"""

from __future__ import annotations

PREAPPROVED_HOSTS: frozenset[str] = frozenset({
    # Anthropic / MCP
    "platform.claude.com",
    "code.claude.com",
    "modelcontextprotocol.io",
    "github.com/anthropics",
    "agentskills.io",
    # Languages
    "docs.python.org",
    "en.cppreference.com",
    "docs.oracle.com",
    "learn.microsoft.com",
    "developer.mozilla.org",
    "go.dev",
    "pkg.go.dev",
    "www.php.net",
    "docs.swift.org",
    "kotlinlang.org",
    "ruby-doc.org",
    "doc.rust-lang.org",
    "www.typescriptlang.org",
    # Web / JS
    "react.dev",
    "angular.io",
    "vuejs.org",
    "nextjs.org",
    "expressjs.com",
    "nodejs.org",
    "bun.sh",
    "jquery.com",
    "getbootstrap.com",
    "tailwindcss.com",
    "d3js.org",
    "threejs.org",
    "redux.js.org",
    "webpack.js.org",
    "jestjs.io",
    "reactrouter.com",
    # Python
    "docs.djangoproject.com",
    "flask.palletsprojects.com",
    "fastapi.tiangolo.com",
    "pandas.pydata.org",
    "numpy.org",
    "www.tensorflow.org",
    "pytorch.org",
    "scikit-learn.org",
    "matplotlib.org",
    "requests.readthedocs.io",
    "jupyter.org",
    # PHP / Java / .NET
    "laravel.com",
    "symfony.com",
    "wordpress.org",
    "docs.spring.io",
    "hibernate.org",
    "tomcat.apache.org",
    "gradle.org",
    "maven.apache.org",
    "asp.net",
    "dotnet.microsoft.com",
    "nuget.org",
    "blazor.net",
    # Mobile
    "reactnative.dev",
    "docs.flutter.dev",
    "developer.apple.com",
    "developer.android.com",
    # ML / data
    "keras.io",
    "spark.apache.org",
    "huggingface.co",
    "www.kaggle.com",
    # Databases
    "www.mongodb.com",
    "redis.io",
    "www.postgresql.org",
    "dev.mysql.com",
    "www.sqlite.org",
    "graphql.org",
    "prisma.io",
    # Cloud / DevOps
    "docs.aws.amazon.com",
    "cloud.google.com",
    "kubernetes.io",
    "www.docker.com",
    "www.terraform.io",
    "www.ansible.com",
    "vercel.com/docs",
    "docs.netlify.com",
    "devcenter.heroku.com",
    # Testing / games / tools
    "cypress.io",
    "selenium.dev",
    "docs.unity.com",
    "docs.unrealengine.com",
    "git-scm.com",
    "nginx.org",
    "httpd.apache.org",
})


def _split_entries() -> tuple[set[str], dict[str, list[str]]]:
    hosts: set[str] = set()
    paths: dict[str, list[str]] = {}
    for entry in PREAPPROVED_HOSTS:
        slash = entry.find("/")
        if slash == -1:
            hosts.add(entry)
        else:
            host, path = entry[:slash], entry[slash:]
            paths.setdefault(host, []).append(path)
    return hosts, paths


_HOSTNAME_ONLY, _PATH_PREFIXES = _split_entries()


def is_preapproved_host(hostname: str, pathname: str) -> bool:
    if hostname in _HOSTNAME_ONLY:
        return True
    prefixes = _PATH_PREFIXES.get(hostname)
    if not prefixes:
        return False
    for prefix in prefixes:
        if pathname == prefix or pathname.startswith(prefix + "/"):
            return True
    return False
