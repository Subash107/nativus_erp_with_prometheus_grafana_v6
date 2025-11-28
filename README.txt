# Nativus ERP (Plain White/Brown Mode)

A simple ERP-style web app for your **Nativus** Shopify store, focused on:

- **Customers** list (Shopify-style fields, search + date filter)
- **Orders** (per customer, amounts, statuses)
- **Expenses / Income** (store finance)
- **Tasks** (follow-ups, customer-related actions)
- **Login / Register** so only you can see the data
- **Export by section & date range** to Excel (`.xlsx`) for:
  - Customers
  - Orders
  - Expenses / Income
  - Tasks

Plain UI in **white / brown mode**, easy on the eyes and minimal.

---

## 1. Run locally (without Docker)

1. Extract the folder, for example:

   `D:\\Works\\nativus_erp_plain`

2. Open PowerShell:

```powershell
cd D:\Works\nativus_erp_plain
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
py app.py
```

3. Open your browser: <http://127.0.0.1:5000>

First screen will be **Login**:
- Click **Register** to create your user.
- Then login to see the **Dashboard**.

Database file: `nativus_erp.db` (SQLite) created in this folder.

---

## 2. Run with Docker Desktop

1. In PowerShell, inside this folder:

```powershell
cd D:\Works\nativus_erp_plain
docker build -t nativus-erp .
docker run -d -p 5000:5000 --name nativus-erp nativus-erp
```

2. Open browser: <http://localhost:5000>

To stop and remove the container:

```powershell
docker stop nativus-erp
docker rm nativus-erp
```

---

## 3. Exports (date-wise, per section)

Each main page has:
- A **date range filter** (`start_date` / `end_date`).
- An **Export** button which downloads only the filtered data.

Routes used for export:

- **Customers:** `/export/customers?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- **Orders:** `/export/orders?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- **Expenses:** `/export/expenses?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&filter_type=expense|income|all`
- **Tasks:** `/export/tasks?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD&status_filter=...`

Excel files are standard `.xlsx` and can be opened in Excel or Google Sheets.

---

## 4. Notes

- This app does **not** call the Shopify API. It is a local ERP where you can store:
  - Shopify customer ID,
  - Orders, tasks, expenses manually.
- Good for keeping your own offline ERP for Nativus.
- To reset everything, stop the app and delete `nativus_erp.db`, then start again.
