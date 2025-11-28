from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    send_file,
    flash,
    session,
)
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from functools import wraps
import os
import io
import pandas as pd
from werkzeug.security import generate_password_hash, check_password_hash
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST


app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
db_file_path = os.path.join(BASE_DIR, "data", "nativus_erp.db")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + db_file_path
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "change-this-secret-key"

db = SQLAlchemy(app)

# ==============================
# MODELS
# ==============================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.Date, default=datetime.utcnow)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    country = db.Column(db.String(100), nullable=True)
    shopify_customer_id = db.Column(db.String(100), nullable=True)
    note = db.Column(db.String(500), nullable=True)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)
    order_date = db.Column(db.Date, default=datetime.utcnow)
    order_number = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    currency = db.Column(db.String(10), nullable=True, default="USD")
    payment_status = db.Column(db.String(50), nullable=True)  # Paid / Pending / Refunded
    fulfillment_status = db.Column(db.String(50), nullable=True)  # Fulfilled / Unfulfilled / Partial
    sales_channel = db.Column(db.String(100), nullable=True)  # Online Store, POS, etc.
    note = db.Column(db.String(500), nullable=True)


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date = db.Column(db.Date, default=datetime.utcnow)
    category = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(20), nullable=False, default="expense")  # expense / income


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=True)
    date = db.Column(db.Date, default=datetime.utcnow)
    title = db.Column(db.String(200), nullable=False)
    status = db.Column(db.String(50), nullable=False, default="Pending")  # Pending / In Progress / Done
    priority = db.Column(db.String(20), nullable=True)  # Low / Medium / High
    note = db.Column(db.String(500), nullable=True)


_db_initialized = False

@app.before_request
def ensure_db_initialized():
    """Ensure SQLite directory + tables exist and tables are ready."""
    global _db_initialized
    if not _db_initialized:
        os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
        db.create_all()
        _db_initialized = True

# ==============================
# HELPERS
# ==============================

def current_user_id():
    return session.get("user_id")


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


def get_basic_stats(user_id: int):
    total_customers = Customer.query.filter_by(user_id=user_id).count()
    total_orders = Order.query.filter_by(user_id=user_id).count()
    total_expense = (
        db.session.query(db.func.coalesce(db.func.sum(Expense.amount), 0.0))
        .filter_by(user_id=user_id, type="expense")
        .scalar()
    )
    total_income = (
        db.session.query(db.func.coalesce(db.func.sum(Expense.amount), 0.0))
        .filter_by(user_id=user_id, type="income")
        .scalar()
    )
    net = total_income - total_expense
    open_tasks = Task.query.filter_by(user_id=user_id).filter(Task.status != "Done").count()
    return {
        "total_customers": total_customers,
        "total_orders": total_orders,
        "total_income": total_income,
        "total_expense": total_expense,
        "net": net,
        "open_tasks": open_tasks,
    }


# ==============================
# PROMETHEUS METRICS
# ==============================

REQUEST_COUNT = Counter(
    "nativus_erp_request_total",
    "Total HTTP requests to the ERP application.",
    ["method", "endpoint"],
)

CUSTOMERS_TOTAL = Gauge(
    "nativus_erp_customers_total",
    "Total customers (for the current dashboard user).",
)

ORDERS_TOTAL = Gauge(
    "nativus_erp_orders_total",
    "Total orders (for the current dashboard user).",
)

INCOME_TOTAL = Gauge(
    "nativus_erp_income_total",
    "Total income amount (for the current dashboard user).",
)

EXPENSE_TOTAL = Gauge(
    "nativus_erp_expense_total",
    "Total expense amount (for the current dashboard user).",
)

OPEN_TASKS_TOTAL = Gauge(
    "nativus_erp_open_tasks_total",
    "Total open tasks (for the current dashboard user).",
)


# Extra, more detailed ERP metrics for "today" view
ORDERS_TODAY = Gauge(
    "nativus_erp_orders_today",
    "Total orders created today (for the current dashboard user).",
)

INCOME_TODAY = Gauge(
    "nativus_erp_income_today",
    "Total income recorded today (for the current dashboard user).",
)

EXPENSE_TODAY = Gauge(
    "nativus_erp_expense_today",
    "Total expenses recorded today (for the current dashboard user).",
)



@app.before_request
def before_request():
    # Count incoming requests except static assets and metrics scraping.
    if request.endpoint not in ("static", "metrics") and request.endpoint is not None:
        try:
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=request.endpoint,
            ).inc()
        except Exception:
            # Metrics must never break the app.
            pass


@app.route("/metrics")
def metrics():
    # Keep ERP-related gauges in sync with the current logged-in user if available.
    try:
        uid = current_user_id()
        if uid:
            stats = get_basic_stats(uid)
            CUSTOMERS_TOTAL.set(stats.get("total_customers", 0))
            ORDERS_TOTAL.set(stats.get("total_orders", 0))
            INCOME_TOTAL.set(stats.get("total_income", 0.0) or 0.0)
            EXPENSE_TOTAL.set(stats.get("total_expense", 0.0) or 0.0)
            OPEN_TASKS_TOTAL.set(stats.get("open_tasks", 0))
            # Extra: today-based metrics for more detailed views.
            today = datetime.utcnow().date()

            orders_today = (
                Order.query.filter_by(user_id=uid)
                .filter(Order.order_date == today)
                .count()
            )
            ORDERS_TODAY.set(orders_today)

            income_today = (
                db.session.query(db.func.coalesce(db.func.sum(Expense.amount), 0.0))
                .filter_by(user_id=uid, type="income")
                .filter(Expense.date == today)
                .scalar()
            )
            INCOME_TODAY.set(income_today or 0.0)

            expense_today = (
                db.session.query(db.func.coalesce(db.func.sum(Expense.amount), 0.0))
                .filter_by(user_id=uid, type="expense")
                .filter(Expense.date == today)
                .scalar()
            )
            EXPENSE_TODAY.set(expense_today or 0.0)

    except Exception:
        # Never let metrics endpoint crash on business logic issues.
        pass

    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}



# ==============================
# AUTH ROUTES
# ==============================

@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("register"))

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return redirect(url_for("register"))

        existing = User.query.filter_by(username=username).first()
        if existing:
            flash("Username already exists.", "danger")
            return redirect(url_for("register"))

        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            flash("Invalid username or password.", "danger")
            return redirect(url_for("login"))

        session["user_id"] = user.id
        session["username"] = user.username
        flash("Logged in.", "success")
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("login"))

# ==============================
# DASHBOARD
# ==============================

@app.route("/")
@login_required
def index():
    user_id = current_user_id()
    stats = get_basic_stats(user_id)

    recent_customers = (
        Customer.query.filter_by(user_id=user_id)
        .order_by(Customer.created_at.desc())
        .limit(5)
        .all()
    )
    recent_orders = (
        Order.query.filter_by(user_id=user_id)
        .order_by(Order.order_date.desc())
        .limit(5)
        .all()
    )
    recent_tasks = (
        Task.query.filter_by(user_id=user_id)
        .order_by(Task.date.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "index.html",
        stats=stats,
        recent_customers=recent_customers,
        recent_orders=recent_orders,
        recent_tasks=recent_tasks,
    )

# ==============================
# CUSTOMERS
# ==============================

@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    user_id = current_user_id()

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        city = request.form.get("city")
        country = request.form.get("country")
        shopify_customer_id = request.form.get("shopify_customer_id")
        note = request.form.get("note")

        if not name:
            flash("Name is required.", "danger")
            return redirect(url_for("customers"))

        c = Customer(
            user_id=user_id,
            name=name,
            email=email,
            phone=phone,
            city=city,
            country=country,
            shopify_customer_id=shopify_customer_id,
            note=note,
        )
        db.session.add(c)
        db.session.commit()
        flash("Customer saved.", "success")
        return redirect(url_for("customers"))

    # filters
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    search = (request.args.get("search") or "").strip()

    query = Customer.query.filter_by(user_id=user_id)

    if search:
        like = f"%%{search.lower()}%%"
        query = query.filter(
            db.or_(
                db.func.lower(Customer.name).like(like),
                db.func.lower(Customer.email).like(like),
                db.func.lower(Customer.phone).like(like),
            )
        )

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            query = query.filter(Customer.created_at >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Customer.created_at <= end_date)
        except ValueError:
            pass

    customers_list = query.order_by(Customer.created_at.desc()).all()

    return render_template(
        "customers.html",
        customers=customers_list,
        start_date=start_date_str,
        end_date=end_date_str,
        search=search,
    )


@app.route("/customers/delete/<int:customer_id>")
@login_required
def delete_customer(customer_id):
    user_id = current_user_id()
    c = Customer.query.filter_by(id=customer_id, user_id=user_id).first_or_404()
    db.session.delete(c)
    db.session.commit()
    flash("Customer deleted.", "info")
    return redirect(url_for("customers"))

# ==============================
# ORDERS
# ==============================

@app.route("/orders", methods=["GET", "POST"])
@login_required
def orders():
    user_id = current_user_id()

    customers = Customer.query.filter_by(user_id=user_id).order_by(Customer.name).all()

    if request.method == "POST":
        order_number = request.form.get("order_number")
        order_date_str = request.form.get("order_date")
        customer_id_str = request.form.get("customer_id")
        total_amount = request.form.get("total_amount")
        currency = request.form.get("currency") or "USD"
        payment_status = request.form.get("payment_status")
        fulfillment_status = request.form.get("fulfillment_status")
        sales_channel = request.form.get("sales_channel")
        note = request.form.get("note")

        if not order_number:
            flash("Order number is required.", "danger")
            return redirect(url_for("orders"))

        try:
            order_date = (
                datetime.strptime(order_date_str, "%Y-%m-%d").date()
                if order_date_str
                else datetime.utcnow().date()
            )
        except ValueError:
            order_date = datetime.utcnow().date()

        try:
            total_amount_val = float(total_amount)
        except (TypeError, ValueError):
            flash("Total amount must be a number.", "danger")
            return redirect(url_for("orders"))

        customer_id = int(customer_id_str) if customer_id_str else None

        o = Order(
            user_id=user_id,
            customer_id=customer_id,
            order_date=order_date,
            order_number=order_number,
            total_amount=total_amount_val,
            currency=currency,
            payment_status=payment_status,
            fulfillment_status=fulfillment_status,
            sales_channel=sales_channel,
            note=note,
        )
        db.session.add(o)
        db.session.commit()
        flash("Order saved.", "success")
        return redirect(url_for("orders"))

    # filters
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    search = (request.args.get("search") or "").strip()

    query = Order.query.filter_by(user_id=user_id)

    if search:
        like = f"%%{search.lower()}%%"
        query = query.filter(
            db.or_(
                db.func.lower(Order.order_number).like(like),
                db.func.lower(Order.sales_channel).like(like),
                db.func.lower(Order.payment_status).like(like),
            )
        )

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            query = query.filter(Order.order_date >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Order.order_date <= end_date)
        except ValueError:
            pass

    orders_list = (
        query.order_by(Order.order_date.desc()).all()
    )

    total_sales = sum(o.total_amount for o in orders_list)

    return render_template(
        "orders.html",
        orders=orders_list,
        customers=customers,
        start_date=start_date_str,
        end_date=end_date_str,
        search=search,
        total_sales=total_sales,
    )


@app.route("/orders/delete/<int:order_id>")
@login_required
def delete_order(order_id):
    user_id = current_user_id()
    o = Order.query.filter_by(id=order_id, user_id=user_id).first_or_404()
    db.session.delete(o)
    db.session.commit()
    flash("Order deleted.", "info")
    return redirect(url_for("orders"))

# ==============================
# EXPENSES
# ==============================

@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses():
    user_id = current_user_id()

    if request.method == "POST":
        date_str = request.form.get("date")
        category = request.form.get("category")
        description = request.form.get("description")
        amount = request.form.get("amount")
        record_type = request.form.get("type", "expense")

        try:
            date_val = (
                datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str
                else datetime.utcnow().date()
            )
        except ValueError:
            date_val = datetime.utcnow().date()

        try:
            amount_val = float(amount)
        except (TypeError, ValueError):
            flash("Amount must be a number.", "danger")
            return redirect(url_for("expenses"))

        e = Expense(
            user_id=user_id,
            date=date_val,
            category=category or "General",
            description=description,
            amount=amount_val,
            type=record_type,
        )
        db.session.add(e)
        db.session.commit()
        flash("Entry saved.", "success")
        return redirect(url_for("expenses"))

    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    filter_type = request.args.get("filter_type", "all")

    query = Expense.query.filter_by(user_id=user_id)
    if filter_type in ("expense", "income"):
        query = query.filter_by(type=filter_type)

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            query = query.filter(Expense.date >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Expense.date <= end_date)
        except ValueError:
            pass

    expenses_list = query.order_by(Expense.date.desc()).all()
    total_amount = sum(e.amount for e in expenses_list)

    return render_template(
        "expenses.html",
        expenses=expenses_list,
        start_date=start_date_str,
        end_date=end_date_str,
        filter_type=filter_type,
        total_amount=total_amount,
    )


@app.route("/expenses/delete/<int:expense_id>")
@login_required
def delete_expense(expense_id):
    user_id = current_user_id()
    e = Expense.query.filter_by(id=expense_id, user_id=user_id).first_or_404()
    db.session.delete(e)
    db.session.commit()
    flash("Entry deleted.", "info")
    return redirect(url_for("expenses"))

# ==============================
# TASKS
# ==============================

@app.route("/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    user_id = current_user_id()
    customers = Customer.query.filter_by(user_id=user_id).order_by(Customer.name).all()

    if request.method == "POST":
        date_str = request.form.get("date")
        title = request.form.get("title")
        customer_id_str = request.form.get("customer_id")
        status = request.form.get("status", "Pending")
        priority = request.form.get("priority")
        note = request.form.get("note")

        if not title:
            flash("Title is required.", "danger")
            return redirect(url_for("tasks"))

        try:
            date_val = (
                datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str
                else datetime.utcnow().date()
            )
        except ValueError:
            date_val = datetime.utcnow().date()

        customer_id = int(customer_id_str) if customer_id_str else None

        t = Task(
            user_id=user_id,
            customer_id=customer_id,
            date=date_val,
            title=title,
            status=status,
            priority=priority,
            note=note,
        )
        db.session.add(t)
        db.session.commit()
        flash("Task saved.", "success")
        return redirect(url_for("tasks"))

    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    status_filter = request.args.get("status_filter", "all")

    query = Task.query.filter_by(user_id=user_id)
    if status_filter != "all":
        query = query.filter_by(status=status_filter)

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            query = query.filter(Task.date >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Task.date <= end_date)
        except ValueError:
            pass

    tasks_list = query.order_by(Task.date.desc()).all()

    return render_template(
        "tasks.html",
        tasks=tasks_list,
        customers=customers,
        start_date=start_date_str,
        end_date=end_date_str,
        status_filter=status_filter,
    )


@app.route("/tasks/delete/<int:task_id>")
@login_required
def delete_task(task_id):
    user_id = current_user_id()
    t = Task.query.filter_by(id=task_id, user_id=user_id).first_or_404()
    db.session.delete(t)
    db.session.commit()
    flash("Task deleted.", "info")
    return redirect(url_for("tasks"))

# ==============================
# EXPORT ROUTES
# ==============================

@app.route("/export/customers")
@login_required
def export_customers():
    user_id = current_user_id()
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")

    query = Customer.query.filter_by(user_id=user_id)
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            query = query.filter(Customer.created_at >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Customer.created_at <= end_date)
        except ValueError:
            pass

    customers = query.order_by(Customer.created_at).all()

    data = [
        {
            "ID": c.id,
            "Created At": c.created_at.strftime("%Y-%m-%d"),
            "Name": c.name,
            "Email": c.email or "",
            "Phone": c.phone or "",
            "City": c.city or "",
            "Country": c.country or "",
            "Shopify Customer ID": c.shopify_customer_id or "",
            "Note": c.note or "",
        }
        for c in customers
    ]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        if data:
            df = pd.DataFrame(data)
            df.to_excel(writer, index=False, sheet_name="Customers")
        else:
            pd.DataFrame(columns=["No data"]).to_excel(
                writer, index=False, sheet_name="Customers"
            )
    output.seek(0)
    filename = f"customers_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/export/orders")
@login_required
def export_orders():
    user_id = current_user_id()
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")

    query = Order.query.filter_by(user_id=user_id)
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            query = query.filter(Order.order_date >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Order.order_date <= end_date)
        except ValueError:
            pass

    orders = query.order_by(Order.order_date).all()

    # map customer names
    customer_map = {
        c.id: c.name
        for c in Customer.query.filter_by(user_id=user_id).all()
    }

    data = [
        {
            "ID": o.id,
            "Order Date": o.order_date.strftime("%Y-%m-%d"),
            "Order Number": o.order_number,
            "Customer": customer_map.get(o.customer_id, "") if o.customer_id else "",
            "Total Amount": o.total_amount,
            "Currency": o.currency or "",
            "Payment Status": o.payment_status or "",
            "Fulfillment Status": o.fulfillment_status or "",
            "Sales Channel": o.sales_channel or "",
            "Note": o.note or "",
        }
        for o in orders
    ]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        if data:
            df = pd.DataFrame(data)
            df.to_excel(writer, index=False, sheet_name="Orders")
        else:
            pd.DataFrame(columns=["No data"]).to_excel(
                writer, index=False, sheet_name="Orders"
            )
    output.seek(0)
    filename = f"orders_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/export/expenses")
@login_required
def export_expenses():
    user_id = current_user_id()
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    filter_type = request.args.get("filter_type", "all")

    query = Expense.query.filter_by(user_id=user_id)
    if filter_type in ("expense", "income"):
        query = query.filter_by(type=filter_type)

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            query = query.filter(Expense.date >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Expense.date <= end_date)
        except ValueError:
            pass

    expenses = query.order_by(Expense.date).all()

    data = [
        {
            "ID": e.id,
            "Date": e.date.strftime("%Y-%m-%d"),
            "Type": e.type,
            "Category": e.category,
            "Description": e.description or "",
            "Amount": e.amount,
        }
        for e in expenses
    ]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        if data:
            df = pd.DataFrame(data)
            df.to_excel(writer, index=False, sheet_name="Expenses")
        else:
            pd.DataFrame(columns=["No data"]).to_excel(
                writer, index=False, sheet_name="Expenses"
            )
    output.seek(0)
    filename = f"expenses_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/export/tasks")
@login_required
def export_tasks():
    user_id = current_user_id()
    start_date_str = request.args.get("start_date", "")
    end_date_str = request.args.get("end_date", "")
    status_filter = request.args.get("status_filter", "all")

    query = Task.query.filter_by(user_id=user_id)
    if status_filter != "all":
        query = query.filter_by(status=status_filter)

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            query = query.filter(Task.date >= start_date)
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            query = query.filter(Task.date <= end_date)
        except ValueError:
            pass

    tasks = query.order_by(Task.date).all()

    customer_map = {
        c.id: c.name
        for c in Customer.query.filter_by(user_id=user_id).all()
    }

    data = [
        {
            "ID": t.id,
            "Date": t.date.strftime("%Y-%m-%d"),
            "Title": t.title,
            "Customer": customer_map.get(t.customer_id, "") if t.customer_id else "",
            "Status": t.status,
            "Priority": t.priority or "",
            "Note": t.note or "",
        }
        for t in tasks
    ]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        if data:
            df = pd.DataFrame(data)
            df.to_excel(writer, index=False, sheet_name="Tasks")
        else:
            pd.DataFrame(columns=["No data"]).to_excel(
                writer, index=False, sheet_name="Tasks"
            )
    output.seek(0)
    filename = f"tasks_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ==============================
# CLI HELPER
# ==============================

@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("Database initialized.")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)
