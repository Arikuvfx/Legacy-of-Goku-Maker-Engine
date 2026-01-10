from setuptools import setup, find_packages

setup(
    name="legacy-goku-engine",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "pygame>=2.5.2",
    ],
    python_requires=">=3.8",
)
