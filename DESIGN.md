## System Architecture

```mermaid
flowchart TD
    U[Business User] -->|Natural language question| UI[Streamlit Frontend]
    UI -->|HTTP request| API[FastAPI Backend]
    API --> V[Input Validation]
    V --> Q[Query and Analytics Logic]
    Q --> DB[(SQLite Database)]
    DB --> Q
    Q --> R[Format Results]
    R --> API
    API -->|JSON response| UI
    UI -->|Answer and supporting data| U

    CSV[Sales CSV Dataset] --> ETL[Data Loading and Cleaning]
    ETL --> DB

    T[Pytest Test Suite] -. Tests .-> API
    T -. Tests .-> Q
```

The Streamlit interface collects business questions and sends them to the FastAPI backend. The backend validates inputs, processes the question using the implemented query and analytics logic, retrieves results from SQLite, and returns a structured response. The sales CSV is loaded and cleaned before being stored in the database. Automated tests verify the backend and analytics logic.
