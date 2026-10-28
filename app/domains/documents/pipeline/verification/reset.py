"""Clear stale admissions on upstream execution; preserve job retry history/feedback."""


def reset_verification() -> dict:
    return {
        "verification_results": [],
        "verified_drafts": [],
        "review_drafts": [],
        "retry_requests": [],
    }
