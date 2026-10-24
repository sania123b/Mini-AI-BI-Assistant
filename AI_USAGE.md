# AI-Assisted Development Report
## Mini AI Business Intelligence Assistant

### 1. AI Tools Used

**ChatGPT**
- Used for understanding the challenge requirements and planning the application architecture.
- Assisted with Python, SQL, FastAPI, and Streamlit implementation and debugging.
- Helped identify edge cases, improve error handling, design tests, and prepare project documentation.

**Claude AI**
- Used as an AI-assisted development resource for reviewing implementation approaches, reasoning about code structure, and exploring potential solutions to programming problems.
- Helped support the development workflow through code analysis and refinement.

*The tools were used as assistants during development. Suggested solutions were reviewed and tested before being incorporated into the project.*

### 2. How AI Was Used During Development

AI assistance helped divide the requirements into smaller components: CSV ingestion, data validation, SQLite database operations, backend API development, natural-language question interpretation, frontend integration, and automated testing.

The tools were used to:
- Explore suitable architecture and implementation approaches.
- Refine Python and SQL logic for business analytics.
- Investigate runtime errors and frontend/backend integration issues.
- Identify invalid data scenarios and potential edge cases.
- Improve testing, error handling, and documentation.

The application separates question interpretation from numerical computation. Analytical results are calculated using database queries or deterministic logic rather than relying on an AI model to invent business figures.

### 3. Problems Encountered

**Python environment issue:** The Uvicorn launcher referenced a Python executable from an earlier environment, preventing the backend from starting.

**API response mismatches:** The Streamlit interface encountered `KeyError` exceptions when expected response fields, including `data` and `warnings`, were missing from certain backend responses.

**Data-quality challenges:** Missing unit prices, zero quantities, and returned transactions required explicit handling to avoid inaccurate revenue calculations.

### 4. How the Problems Were Debugged

I inspected the error tracebacks to identify the failing files and lines, checked the active Python environment, and used `python -m uvicorn` to run the backend through the active interpreter.

For API integration issues, I examined the actual backend responses and compared their structure with the fields expected by Streamlit. This helped identify mismatches and improve response handling.

I also reviewed data-validation rules and used automated tests to verify expected behavior and detect regressions.

### 5. Key Learnings

The project strengthened my understanding of backend development, database-backed analytics, API integration, AI-assisted programming, and automated testing.

A key design decision was to separate AI-assisted interpretation from deterministic analytics. This improves traceability and reduces the risk of fabricated numerical results.

I also learned that AI-generated code must be reviewed, tested, and debugged rather than accepted without verification.

### 6. What I Would Improve With Another Day

With additional time, I would expand natural-language question coverage, improve handling of ambiguous questions, strengthen end-to-end testing, enhance data-quality reporting, and explore retrieval-based context for business definitions and database schemas.

I would also improve deployment readiness, logging, and configuration management.

### Conclusion

ChatGPT and Claude AI supported different aspects of the development and problem-solving process. The project helped me apply AI-assisted development while maintaining a focus on code correctness, deterministic analytics, testing, and explainable technical decisions.
