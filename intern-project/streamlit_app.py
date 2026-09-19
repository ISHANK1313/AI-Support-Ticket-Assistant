"""HTTP-only, per-session Streamlit frontend."""
import os
from decimal import Decimal, InvalidOperation

import streamlit as st

from src.ui_client import APIClient, APIError, ticket_payload


def clear_private_state():
    # No cache decorators: tokens, forms and responses belong to this session only.
    for key in list(st.session_state):
        del st.session_state[key]


def client():
    return APIClient(os.environ.get("API_BASE_URL", "http://127.0.0.1:8000"),
                     token=st.session_state.get("token"), on_unauthorized=clear_private_state)


def show_error(error):
    if error.status == 401:
        st.session_state["notice"] = str(error)
        st.rerun()
    st.error(str(error))


def render_ticket(ticket):
    st.subheader(f"Ticket #{ticket['id']}")
    st.caption(ticket.get("created_at", ""))
    st.text(ticket.get("message", ""))
    with st.expander("Submitted facts (null means unknown)"):
        st.json(ticket.get("facts", {}))
    decision = ticket.get("decision")
    if not decision:
        st.warning("No decision was returned for this ticket.")
        return
    st.markdown("**Recommended action**")
    st.text(decision["action"])
    st.metric("Model-reported confidence", f"{decision['confidence']:.0%}")
    st.caption("Not a calibrated probability or a guarantee. No order action is executed.")
    st.markdown("**Reason / requested clarification**")
    st.text(decision["reason"])
    st.markdown("**Policy sources**")
    st.text(", ".join(decision.get("sources", [])) or "No sources provided")


def account():
    login, register = st.tabs(["Sign in", "Register"])
    with login, st.form("login_form", clear_on_submit=True):
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        if st.form_submit_button("Sign in", key="login_submit"):
            try:
                api = client()
                result = api.login(email.strip(), password)
                identity = api.me()
                clear_private_state()
                st.session_state["token"] = result["access_token"]
                st.session_state["identity"] = identity
                st.rerun()
            except APIError as error:
                show_error(error)
    with register, st.form("register_form", clear_on_submit=True):
        email = st.text_input("Email", key="register_email")
        password = st.text_input("Password (8–128 characters)", type="password", key="register_password")
        if st.form_submit_button("Create account", key="register_submit"):
            try:
                client().register(email.strip(), password)
                st.success("Account created. Sign in to continue.")
            except APIError as error:
                show_error(error)


def new_decision():
    st.header("New decision")
    st.caption("Enter only known facts. Leave numbers blank for unknown; zero is a real value.")
    with st.form("ticket_form"):
        message = st.text_area("Complaint message", max_chars=5000, key="message")
        # Text avoids binary float rounding for Decimal money, including exact boundaries.
        value = st.text_input("Order value in INR (optional)", key="value")
        delivery = st.number_input("Days since delivery", min_value=0, step=1, value=None, key="delivery")
        dispatch = st.number_input("Days since dispatch", min_value=0, step=1, value=None, key="dispatch")
        product = st.selectbox("Product type", ["unknown", "food", "non_food", "mixed"])
        opened = st.selectbox("Opened status", ["unknown", "opened", "unopened"])
        status = st.selectbox("Order status", ["unknown", "processing", "dispatched", "delivered"])
        ordered = st.text_input("Ordered item (optional)", max_chars=200)
        received = st.text_input("Received item (optional)", max_chars=200)
        available = st.selectbox("Original item available", ["unknown", "yes", "no"])
        submit = st.form_submit_button("Get recommendation", key="ticket_submit")
    if submit:
        st.session_state.pop("decision_result", None)
        try:
            money = Decimal(value.strip()) if value.strip() else None
            if not message.strip():
                raise ValueError("Enter a complaint message.")
            if money is not None and (not money.is_finite() or money < 0 or money.as_tuple().exponent < -2):
                raise ValueError("Enter a nonnegative value with at most two decimal places.")
            payload = ticket_payload(message=message, order_value_inr=money,
                                     days_since_delivery=delivery, days_since_dispatch=dispatch,
                                     product_type=product, opened_status=opened, order_status=status,
                                     ordered_item=ordered.strip() or None, received_item=received.strip() or None,
                                     original_item_available={"unknown": None, "yes": True, "no": False}[available])
            with st.spinner("Reviewing policies. This may take up to two minutes."):
                result = client().create_ticket(payload)
            st.session_state["decision_result"] = result
            st.session_state.pop("history", None)
        except InvalidOperation:
            st.error("Enter a valid decimal order value, or leave it blank.")
        except ValueError as error:
            st.error(str(error))
        except APIError as error:
            show_error(error)
    if "decision_result" in st.session_state:
        render_ticket(st.session_state["decision_result"])


def history():
    st.header("Your saved history")
    with st.form("history_form"):
        limit = st.selectbox("Page size", [20, 5, 10, 50])
        page = st.number_input("Page (starting at 1)", min_value=1, step=1, value=1)
        load = st.form_submit_button("Load / refresh page", key="history_load")
    if load:
        st.session_state.pop("history", None)
        st.session_state.pop("detail", None)
        try:
            st.session_state["history"] = client().list_tickets(limit=limit, offset=(page - 1) * limit)
        except APIError as error:
            show_error(error)
    saved = st.session_state.get("history")
    if saved is None:
        st.info("Load a page to view tickets belonging to your account.")
        return
    st.caption(f"Showing saved page at offset {saved['offset']}, limit {saved['limit']}.")
    items = saved["items"]
    if not items:
        st.info("No tickets on this page. Try an earlier page or submit your first ticket.")
        return
    labels = {item["id"]: f"#{item['id']} — {item['created_at']}" for item in items}
    with st.form("detail_form"):
        selected = st.selectbox("Ticket", list(labels), format_func=labels.get)
        load_detail = st.form_submit_button("View saved decision", key="history_detail")
    if load_detail:
        st.session_state.pop("detail", None)
        try:
            st.session_state["detail"] = client().get_ticket(selected)
        except APIError as error:
            show_error(error)
    if "detail" in st.session_state:
        render_ticket(st.session_state["detail"])


def main():
    st.set_page_config(page_title="Support Ticket Assistant", layout="centered")
    st.title("Support Ticket Assistant")
    st.caption("Policy-grounded recommendations, not automatic refunds or cancellations.")
    if notice := st.session_state.pop("notice", None):
        st.warning(notice)
    if not st.session_state.get("token"):
        account()
        return
    st.sidebar.text(st.session_state.get("identity", {}).get("email", "Signed in"))
    if st.sidebar.button("Sign out", key="logout"):
        clear_private_state()
        st.rerun()
    page = st.sidebar.radio("Workspace", ["New decision", "History"], key="page")
    if page == "New decision":
        new_decision()
    else:
        history()


if __name__ == "__main__":
    main()
