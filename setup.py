import setuptools

with open("README.md", "r") as fh:

    long_description = fh.read()

setuptools.setup(

    name="rouls", # Replace with your username

    version="1.0.0",

    author="MFALLAN",

    packages=setuptools.find_packages(),
    
    classifiers=[

        "Programming Language :: Python :: 3",

        "License :: OSI Approved :: MIT License",

        "Operating System :: OS Independent",

    ],


    python_requires='>=3.6',

)
