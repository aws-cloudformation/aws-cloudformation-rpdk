#!/usr/bin/env python
import os.path
import re

from setuptools import setup

HERE = os.path.abspath(os.path.dirname(__file__))


def read(*parts):
    with open(os.path.join(HERE, *parts), "r", encoding="utf-8") as fp:
        return fp.read()


# https://packaging.python.org/guides/single-sourcing-package-version/
def find_version(*file_paths):
    version_file = read(*file_paths)
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]", version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")


setup(
    name="cloudformation-cli",
    version=find_version("src", "rpdk", "core", "__init__.py"),
    description=__doc__,
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    author="Amazon Web Services",
    author_email="aws-cloudformation-developers@amazon.com",
    url="https://github.com/aws-cloudformation/aws-cloudformation-rpdk/",
    # https://packaging.python.org/guides/packaging-namespace-packages/
    packages=["rpdk.core"],
    package_dir={"": "src"},
    # package_data -> use MANIFEST.in instead
    include_package_data=True,
    zip_safe=True,
    python_requires=">=3.6",
    install_requires=[
        "boto3>=1.10.20",
        "Jinja2>=3.1.2",
        "markupsafe>=2.1.0",
        "jsonpatch",
        "jsonschema>=3.0.0,<=4.17.3",
        "pytest>=4.5.0",
        "pytest-random-order>=1.0.4",
        "pytest-localserver>=0.5.0",
        "Werkzeug>=2.2.0",
        "PyYAML>=6.0.1",
        "requests>=2.22",
        "hypothesis>=4.32",
        "colorama>=0.4.1",
        "docker>=4.3.1",
        "ordered-set>=4.0.2",
        "cfn-lint>=0.78.1",
        "cfn_flip>=1.2.3",
        "nested-lookup",
        "botocore>=1.31.17",
    ],
    entry_points={
        "console_scripts": ["cfn-cli = rpdk.core.cli:main", "cfn = rpdk.core.cli:main"]
    },
    license="Apache License 2.0",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
        "Topic :: Software Development :: Build Tools",
        "Topic :: Software Development :: Code Generators",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    keywords="Amazon Web Services AWS CloudFormation",
)
