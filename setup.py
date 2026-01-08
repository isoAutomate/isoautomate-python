from setuptools import setup, find_packages

setup(
    name="isoautomate",
    version="0.1.3",
    packages=find_packages(),
    install_requires=[
        "redis",
        "python-dotenv",
    ],
    author="isoAutomate",
    description="Sovereign Browser Infrastructure SDK",
    long_description=open("README.md").read() if open("README.md") else "",
    long_description_content_type="text/markdown",
    url="https://github.com/isoautomate/isoautomate-python",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.7',
)