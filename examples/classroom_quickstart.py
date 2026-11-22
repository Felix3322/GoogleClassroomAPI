#!/usr/bin/env python3
"""Try the cookie-based Classroom API against your own test class.

    python examples/classroom_quickstart.py cookies.txt <any link of the course>

Reads the course and its students, creates a DRAFT assignment (students cannot
see drafts), shows it in the Classwork listing, then deletes it again.
cookies.txt comes from tools/cookie-export/ — treat it like a password.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from google_classroom_api import Classroom  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    cookie_file, link = sys.argv[1], sys.argv[2]
    cls = Classroom.from_cookie_file(cookie_file)
    course = Classroom.course_id_from_url(link)

    print("course:  ", cls.course(course))
    for student in cls.students(course):
        print("student: ", student["name"], student["email"])

    draft = cls.create_assignment(course, f"GoogleClassroomAPI demo {int(time.time())}",
                                  instructions="Created by the quickstart; it deletes itself.",
                                  publish=False)
    print("draft:   ", draft)
    listed = [a for a in cls.assignments(course) if a["item_id"] == str(draft["item_id"])]
    print("listed:  ", listed)
    cls.delete_assignment(course, int(draft["item_id"]))
    print("deleted: ", all(a["item_id"] != str(draft["item_id"]) for a in cls.assignments(course)))


if __name__ == "__main__":
    main()
