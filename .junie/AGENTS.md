Your goal is to write the highest quality code possible.
This means that you spend extra time reading and understanding
existing code. If you are not very certain of a task or a choice,
you will ask.

Whenever possible, you will avoid re-generating a code file. Generating
patches to existing code is highly preferred to replacing a file with new code.
If you need to regenerate a file, please confirm with me before proceeding.

Please use the Python environment that can be accessed using `conda activate arcgispro-py3` for this
project. You will not be able to install packages in this environment, so please confirm with me
before you decide on a workflow that requires a new package. The environment comes with many
packages beyond the standard library though, and may be sufficient for many workflows.

All generated code should have unit tests added. Additionally,
any other potentially relevant tests, including integration or
end to end tests should be created. Please do not
declare the task complete until you have generated all new tests,
patched any existing tests as necessary, and run all tests to confirm
that they pass.

For this project, in addition to unit tests, please also include data quality tests - some kind
of test that, after running the build process, confirms that the data were correctly merged
and that queries against the dataset return expected results. These tests will likely need
to use Esri's arcpy library to access the source data and perform queries against the dataset or
checks against schema. Where possible, please also test performance when testing the data quality.
pandas is available in the project environment, and there are arcpy workflows to convert through to pandas
data frames if that would be most appropriate.

If tests do not pass and you think changes to a configuration file would fix the test,
please ask me whether you should update the configuration file or update the test.

Always place a summary of the completed work in the .junie/reports folder,
as a markdown file with the word `summary` in the name and the date and time
it was generated to distinguish it from other files (e.g. `summary_<date>_<time>.md`),
with the input prompt, how you understood it, and any actions taken or code generated.

Prefer clear, expressive code over short code. Include comments
regularly throughout all code to express the intent of items. Variable names
should clearly express the intent and usage of the variables.

When investigating potential approaches to a request, feature, or problem, please output scripts
into .junie/investigations and bundle multiple into a single request when possible. That will reduce
the number of approval prompts, which will reduce wait time if I do not approve immediately. Please only
bundle requests to run code if each one may add information you need, not as a contingency for one working and the
other not working (to reduce tokens). Bundle requests to run code if they are related to the same problem or feature.
Please name the code with a sequential number, starting with 1 and a short description (e.g. 1_<description>.py).

Write documentation as markdown in the `documentation` folder. For interactive elements,
write documentation for an end user. For API elements, write documentation for a developer describing
key points to interface with the code, how to use it, and considerations.

Finally, maintain a running feature list file in the main folder as FEATURES.md - before
making modifications, consult this file to understand what existing features
should be maintained. When adding a feature, add a line to the file with more
information and confirm that no prior feature has been improperly changed by comparing
the feature list with the code.
