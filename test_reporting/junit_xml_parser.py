"""Utilities for validating and parsing JUnit XML files generated by Pytest and Spytest.

This library/script should work for any test result XML file generated by Pytest or Spytest.

CLI Usage:
% python3 junit_xml_parser.py -h
usage: junit_xml_parser.py [-h] [--validate-only] [--compact] [--output-file OUTPUT_FILE] file

Validate and convert SONiC JUnit XML files into JSON.

positional arguments:
file                  A file to validate/parse.

optional arguments:
-h, --help            show this help message and exit
--validate-only       Validate without parsing the file.
--compact, -c         Output the JSON in a compact form.
--output-file OUTPUT_FILE, -o OUTPUT_FILE
                        A file to store the JSON output in.

Examples:
python3 junit_xml_parser.py tests/files/sample_tr.xml
"""
import argparse
import json
import sys
import os

from collections import defaultdict

import defusedxml.ElementTree as ET


MAXIMUM_XML_SIZE = 10e7  # 10MB

# Fields found in the testsuite/root section of the JUnit XML file.
TESTSUITE_TAG = "testsuite"
REQUIRED_TESTSUITE_ATTRIBUTES = {
    "time",
    "tests",
    "skipped",
    "failures",
    "errors",
}

# Fields found in the metadata/properties section of the JUnit XML file.
METADATA_TAG = "properties"
METADATA_PROPERTY_TAG = "property"
REQUIRED_METADATA_PROPERTIES = [
    "topology",
    "markers",
    "host",
    "asic",
    "platform",
    "hwsku",
    "os_version",
]

# Fields found in the testcase sections of the JUnit XML file.
TESTCASE_TAG = "testcase"
REQUIRED_TESTCASE_ATTRIBUTES = [
    "classname",
    "file",
    "line",
    "name",
    "time",
]


class JUnitXMLValidationError(Exception):
    """Expected errors that are thrown while validating the contents of the JUnit XML file."""


def validate_junit_xml_stream(stream):
    """Validate that a stream containing an XML document is valid JUnit XML.

    Args:
        stream: A string containing an XML document.

    Returns:
        The root of the validated XML document.

    Raises:
        JUnitXMLValidationError: if any of the following are true:
            - The provided stream exceeds 10MB
            - The provided stream is unparseable
            - The provided stream is missing required fields
    """
    if sys.getsizeof(stream) > MAXIMUM_XML_SIZE:
        raise JUnitXMLValidationError("provided stream is too large")

    try:
        root = ET.fromstring(stream, forbid_dtd=True)
    except Exception as e:
        raise JUnitXMLValidationError("could not parse provided XML stream") from e

    return _validate_junit_xml(root)


def validate_junit_xml_file(document_name):
    """Validate that an XML file is valid JUnit XML.

    Args:
        document_name: The name of the document.

    Returns:
        The root of the validated XML document.

    Raises:
        JUnitXMLValidationError: if any of the following are true:
            - The provided file doesn't exist
            - The provided file exceeds 10MB
            - The provided file is unparseable
            - The provided file is missing required fields
    """
    if not os.path.exists(document_name) or not os.path.isfile(document_name):
        raise JUnitXMLValidationError("file not found")

    if os.path.getsize(document_name) > MAXIMUM_XML_SIZE:
        raise JUnitXMLValidationError("provided file is too large")

    try:
        tree = ET.parse(document_name, forbid_dtd=True)
    except Exception as e:
        raise JUnitXMLValidationError("could not parse provided XML document") from e

    return _validate_junit_xml(tree.getroot())


def _validate_junit_xml(root):
    _validate_test_summary(root)
    _validate_test_metadata(root)
    _validate_test_cases(root)

    return root


def _validate_test_summary(root):
    if root.tag != TESTSUITE_TAG:
        raise JUnitXMLValidationError(f"{TESTSUITE_TAG} tag not found on root element")

    for xml_field in REQUIRED_TESTSUITE_ATTRIBUTES:
        if xml_field not in root.keys():
            raise JUnitXMLValidationError(f"{xml_field} not found in <{TESTSUITE_TAG}> element")

        try:
            float(root.get(xml_field))
        except Exception as e:
            raise JUnitXMLValidationError(
                f"invalid type for {xml_field} in {TESTSUITE_TAG}> element: "
                f"expected a number, received "
                f'"{root.get(xml_field)}"'
            ) from e


def _validate_test_metadata(root):
    properties_element = root.find("properties")

    if not properties_element:
        raise JUnitXMLValidationError(f"metadata element <{METADATA_TAG}> not found")

    seen_properties = []
    for prop in properties_element.iterfind(METADATA_PROPERTY_TAG):
        property_name = prop.get("name", None)

        if not property_name:
            raise JUnitXMLValidationError(
                f'invalid metadata element: "name" not found in '
                f"<{METADATA_PROPERTY_TAG}> element"
            )

        if property_name not in REQUIRED_METADATA_PROPERTIES:
            raise JUnitXMLValidationError(f"unexpected metadata element: {property_name}")

        if property_name in seen_properties:
            raise JUnitXMLValidationError(
                f"duplicate metadata element: {property_name} seen more than once"
            )

        property_value = prop.get("value", None)

        if property_value is None:  # Some fields may be empty
            raise JUnitXMLValidationError(
                f'invalid metadata element: no "value" field provided for {property_name}'
            )

        seen_properties.append(property_name)


def _validate_test_cases(root):
    def _validate_test_case(test_case):
        for attribute in REQUIRED_TESTCASE_ATTRIBUTES:
            if attribute not in test_case.keys():
                raise JUnitXMLValidationError(
                    f'"{attribute}" not found in test case '
                    f"\"{test_case.get('name', 'Name Not Found')}\""
                )

            # NOTE: "if failure" and "if error" does not work with the ETree library.
            failure = test_case.find("failure")
            if failure is not None and failure.get("message") is None:
                raise JUnitXMLValidationError(
                    f"no message found for failure in \"{test_case.get('name')}\""
                )

            error = test_case.find("error")
            if error is not None and not error.get("message"):
                raise JUnitXMLValidationError(
                    f"no message found for error in \"{test_case.get('name')}\""
                )

    cases = root.findall(TESTCASE_TAG)
    if len(cases) <= 0:
        raise JUnitXMLValidationError("No test cases found")

    for test_case in cases:
        _validate_test_case(test_case)


def parse_test_result(root):
    """Parse a given XML document into JSON.

    Args:
        root: The root of the XML document to parse.

    Returns:
        A dict containing the parsed test result.
    """
    test_result_json = {}

    test_result_json["test_summary"] = _parse_test_summary(root)
    test_result_json["test_metadata"] = _parse_test_metadata(root)
    test_result_json["test_cases"] = _parse_test_cases(root)

    return test_result_json


def _parse_test_summary(root):
    test_result_summary = {}
    for attribute in REQUIRED_TESTSUITE_ATTRIBUTES:
        test_result_summary[attribute] = root.get(attribute)

    return test_result_summary


def _parse_test_metadata(root):
    properties_element = root.find(METADATA_TAG)
    test_result_metadata = {}

    for prop in properties_element.iterfind("property"):
        if prop.get("value"):
            test_result_metadata[prop.get("name")] = prop.get("value")

    return test_result_metadata


def _parse_test_cases(root):
    test_case_results = defaultdict(list)

    def _parse_test_case(test_case):
        result = {}

        test_class_tokens = test_case.get("classname").split(".")
        feature = test_class_tokens[0]

        for attribute in REQUIRED_TESTCASE_ATTRIBUTES:
            result[attribute] = test_case.get(attribute)

        # NOTE: "if failure" and "if error" does not work with the ETree library.
        failure = test_case.find("failure")
        if failure is not None:
            result["failure"] = failure.get("message")

        error = test_case.find("error")
        if error is not None:
            result["error"] = error.get("message")

        return feature, result

    for test_case in root.findall("testcase"):
        feature, result = _parse_test_case(test_case)
        test_case_results[feature].append(result)

    return dict(test_case_results)


def _run_script():
    parser = argparse.ArgumentParser(
        description="Validate and convert SONiC JUnit XML files into JSON.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Examples:
python3 junit_xml_parser.py tests/files/sample_tr.xml
""",
    )
    parser.add_argument("file_name", metavar="file", type=str, help="A file to validate/parse.")
    parser.add_argument(
        "--validate-only", action="store_true", help="Validate without parsing the file.",
    )
    parser.add_argument(
        "--compact", "-c", action="store_true", help="Output the JSON in a compact form.",
    )
    parser.add_argument(
        "--output-file", "-o", type=str, help="A file to store the JSON output in.",
    )

    args = parser.parse_args()

    try:
        root = validate_junit_xml_file(args.file_name)
    except JUnitXMLValidationError as e:
        print(f"XML validation failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error occured during validation: {e}")
        sys.exit(2)

    if args.validate_only:
        print(f"{args.file_name} validated succesfully!")
        sys.exit(0)

    test_result_json = parse_test_result(root)

    if args.compact:
        output = json.dumps(test_result_json, separators=(",", ":"), sort_keys=True)
    else:
        output = json.dumps(test_result_json, indent=4, sort_keys=True)

    if args.output_file:
        with open(args.output_file, "w+") as output_file:
            output_file.write(output)
    else:
        print(output)


if __name__ == "__main__":
    _run_script()
