import re
import zipfile
from io import BytesIO
from unittest import TestCase

import frappe
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import (
	create_sales_invoice,
)
from frappe.utils import cstr, now_datetime, today

from erpnext_datev.erpnext_datev.report.datev.datev import (
	download_datev_csv,
	get_account_names,
	get_customers,
	get_parties_without_number,
	get_purchase_invoice_params,
	get_suppliers,
	get_transactions,
)
from erpnext_datev.utils.datev_constants import (
	AccountNames,
	DebtorsCreditors,
	Transactions,
)
from erpnext_datev.utils.datev_csv import get_datev_csv, get_header


def make_company(company_name, abbr):
	if not frappe.db.exists("Company", company_name):
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": company_name,
				"abbr": abbr,
				"default_currency": "EUR",
				"country": "Germany",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "SKR04 mit Kontonummern",
			}
		)
		company.insert()
	else:
		company = frappe.get_doc("Company", company_name)

	# indempotent
	company.create_default_warehouses()

	if not frappe.db.get_value("Cost Center", {"is_group": 0, "company": company.name}):
		company.create_default_cost_center()

	company.save()
	return company


def setup_fiscal_year():
	# ERPNext v16 removed FiscalYear.set_as_default(); the year is found by date.
	year = cstr(now_datetime().year)
	if not frappe.db.get_value("Fiscal Year", {"year": year}, "name"):
		try:
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": year,
					"year_start_date": f"{year}-01-01",
					"year_end_date": f"{year}-12-31",
				}
			).insert()
		except frappe.NameError:
			pass


def make_customer_with_account(customer_name, company):
	acc_name = frappe.db.get_value(
		"Account", {"account_name": customer_name, "company": company.name}, "name"
	)

	if not acc_name:
		acc = frappe.get_doc(
			{
				"doctype": "Account",
				"parent_account": "1 - Forderungen aus Lieferungen und Leistungen - _TG",
				"account_name": customer_name,
				"company": company.name,
				"account_type": "Receivable",
				"account_number": "10001",
			}
		)
		acc.insert()
		acc_name = acc.name

	if not frappe.db.exists("Customer", customer_name):
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer_name,
				"customer_type": "Company",
				"accounts": [{"company": company.name, "account": acc_name}],
			}
		)
		customer.insert()
	else:
		customer = frappe.get_doc("Customer", customer_name)

	return customer


def make_item(item_code, company):
	warehouse_name = frappe.db.get_value(
		"Warehouse", {"warehouse_name": "Stores", "company": company.name}, "name"
	)

	if not frappe.db.exists("Item", item_code):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"description": item_code,
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_purchase_item": 0,
				"is_customer_provided_item": 0,
				"item_defaults": [{"default_warehouse": warehouse_name, "company": company.name}],
			}
		)
		item.insert()
	else:
		item = frappe.get_doc("Item", item_code)
	return item


def make_datev_settings(company):
	if not frappe.db.exists("DATEV Settings", company.name):
		frappe.get_doc(
			{
				"doctype": "DATEV Settings",
				"client": company.name,
				"client_number": "12345",
				"consultant_number": "67890",
				"temporary_against_account_number": "9999",
			}
		).insert()


class TestDatev(TestCase):
	def setUp(self):
		self.company = make_company("_Test GmbH", "_TG")
		self.customer = make_customer_with_account("_Test Kunde GmbH", self.company)
		self.filters = {
			"company": self.company.name,
			"from_date": today(),
			"to_date": today(),
			"temporary_against_account_number": "9999",
			# used by the transaction query
			"against_account": "9999",
			"opening_account": "9999",
		}

		make_datev_settings(self.company)
		item = make_item("_Test Item", self.company)
		setup_fiscal_year()

		warehouse = frappe.db.get_value(
			"Item Default",
			{"parent": item.name, "company": self.company.name},
			"default_warehouse",
		)

		income_account = frappe.db.get_value(
			"Account", {"account_number": "4200", "company": self.company.name}, "name"
		)

		tax_account = frappe.db.get_value(
			"Account", {"account_number": "3806", "company": self.company.name}, "name"
		)

		si = create_sales_invoice(
			company=self.company.name,
			customer=self.customer.name,
			currency=self.company.default_currency,
			debit_to=self.customer.accounts[0].account,
			income_account=income_account,
			expense_account="6990 - Herstellungskosten - _TG",
			cost_center=self.company.cost_center,
			warehouse=warehouse,
			item=item.name,
			do_not_save=1,
		)

		si.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": tax_account,
				"description": "Umsatzsteuer 19 %",
				"rate": 19,
				"cost_center": self.company.cost_center,
			},
		)

		si.cost_center = self.company.cost_center

		si.save()
		si.submit()
		self.sales_invoice = si

	def test_columns(self):
		def is_subset(get_data, allowed_keys):
			"""
			Validate that the dict contains only allowed keys.

			Params:
			get_data -- Function that returns a list of dicts.
			allowed_keys -- List of allowed keys
			"""
			data = get_data(self.filters)
			if data == []:
				# No data and, therefore, no columns is okay
				return True
			actual_set = set(data[0].keys())
			# allowed set must be interpreted as unicode to match the actual set
			allowed_set = set({frappe.as_unicode(key) for key in allowed_keys})
			return actual_set.issubset(allowed_set)

		self.assertTrue(is_subset(get_transactions, Transactions.COLUMNS))
		self.assertTrue(is_subset(get_customers, DebtorsCreditors.COLUMNS))
		self.assertTrue(is_subset(get_suppliers, DebtorsCreditors.COLUMNS))
		self.assertTrue(is_subset(get_account_names, AccountNames.COLUMNS))

	def test_header(self):
		self.assertTrue(Transactions.DATA_CATEGORY in get_header(self.filters, Transactions))
		self.assertTrue(AccountNames.DATA_CATEGORY in get_header(self.filters, AccountNames))
		self.assertTrue(DebtorsCreditors.DATA_CATEGORY in get_header(self.filters, DebtorsCreditors))

	def test_csv(self):
		test_data = [
			{
				"Umsatz (ohne Soll/Haben-Kz)": 100,
				"Soll/Haben-Kennzeichen": "H",
				"Kontonummer": "4200",
				"Gegenkonto (ohne BU-Schlüssel)": "10000",
				"Belegdatum": today(),
				"Buchungstext": "No remark",
				"Beleginfo - Art 1": "Sales Invoice",
				"Beleginfo - Inhalt 1": "SINV-0001",
			}
		]
		get_datev_csv(data=test_data, filters=self.filters, csv_class=Transactions)

	def test_download(self):
		"""Assert that the returned file is a ZIP file."""
		download_datev_csv(self.filters)

		# zipfile.is_zipfile() expects a file-like object
		zip_buffer = BytesIO()
		zip_buffer.write(frappe.response["filecontent"])

		self.assertTrue(zipfile.is_zipfile(zip_buffer))

	def test_cancelled_entries_are_excluded(self):
		"""A cancelled invoice and its reverse rows must not be exported."""
		si = frappe.copy_doc(self.sales_invoice)
		si.submit()
		self.assertIn(si.name, get_voucher_numbers(self.filters))

		si.reload()
		si.cancel()
		self.assertNotIn(si.name, get_voucher_numbers(self.filters))
		self.assertIn(self.sales_invoice.name, get_voucher_numbers(self.filters))

	def test_parties_are_limited_to_company(self):
		"""Only parties with a Debtor/Creditor Number for this company are exported."""
		other_company = make_company("_Test Andere GmbH", "_TA")
		receivable = get_receivable_account(self.company)
		other_receivable = get_receivable_account(other_company)

		make_party("Customer", "_Test DATEV Kunde Eigen", {self.company.name: (receivable, "10101")})
		make_party("Customer", "_Test DATEV Kunde Fremd", {other_company.name: (other_receivable, "10102")})
		make_party("Customer", "_Test DATEV Kunde Ohne Nummer", {self.company.name: (receivable, "")})

		konten = {row["Konto"] for row in get_customers(self.filters)}
		self.assertIn("10101", konten)
		self.assertNotIn("10102", konten)
		self.assertNotIn(None, konten)
		self.assertNotIn("", konten)

	def test_party_without_primary_address(self):
		"""Parties without a primary address are exported, with their other address or none."""
		receivable = get_receivable_account(self.company)
		# new parties and numbers on every run, because test data is not rolled back
		suffix = frappe.generate_hash(length=6)
		no_address = f"2{suffix}"
		other_address = f"3{suffix}"
		make_party(
			"Customer", f"_Test DATEV Kunde Keine Adresse {suffix}", {self.company.name: (receivable, no_address)}
		)
		party = make_party(
			"Customer", f"_Test DATEV Kunde Nebenadresse {suffix}", {self.company.name: (receivable, other_address)}
		)
		make_address("Customer", party.name, "Nebenstraße 2", is_primary=0)

		rows = {row["Konto"]: row for row in get_customers(self.filters)}
		self.assertIn(no_address, rows)
		self.assertIsNone(rows[no_address]["Straße"])
		self.assertIn(other_address, rows)
		self.assertEqual(rows[other_address]["Straße"], "Nebenstraße 2")

		# one row per party, even with several addresses
		make_address("Customer", party.name, "Hauptstraße 1", is_primary=1)
		rows = [row for row in get_customers(self.filters) if row["Konto"] == other_address]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["Straße"], "Hauptstraße 1")

	def test_parties_without_number_are_reported(self):
		"""The setup customer has bookings but no Debtor/Creditor Number."""
		parties = {(p.party_type, p.party) for p in get_parties_without_number(self.filters)}
		self.assertIn(("Customer", self.customer.name), parties)

	def test_due_date_can_be_excluded(self):
		frappe.db.set_value("DATEV Settings", self.company.name, "exclude_due_date", 0)
		rows = get_sales_invoice_rows(self.filters, self.sales_invoice.name)
		self.assertTrue(all(row["Fälligkeit"] for row in rows))

		frappe.db.set_value("DATEV Settings", self.company.name, "exclude_due_date", 1)
		rows = get_sales_invoice_rows(self.filters, self.sales_invoice.name)
		self.assertTrue(rows)
		self.assertFalse(any(row["Fälligkeit"] for row in rows))

	def test_beleginfo_art_max_length(self):
		# fixed labels of the purchase invoice rows
		extra_fields = get_purchase_invoice_params(frappe._dict(self.filters))[0]
		for label in re.findall(r"'([^']*)' as 'Beleginfo - Art \d'", extra_fields):
			self.assertLessEqual(len(label), 20, label)

		for row in get_transactions(self.filters):
			for i in range(1, 7):
				value = row.get(f"Beleginfo - Art {i}") or ""
				self.assertLessEqual(len(value), 20, f"Beleginfo - Art {i}: {value}")


def get_voucher_numbers(filters):
	return {row["Belegfeld 1"] for row in get_transactions(frappe._dict(filters))}


def get_sales_invoice_rows(filters, name):
	return [row for row in get_transactions(frappe._dict(filters)) if row["Belegfeld 1"] == name]


def get_receivable_account(company):
	return frappe.db.get_value(
		"Account", {"company": company.name, "account_type": "Receivable", "is_group": 0}, "name"
	)


def make_party(party_type, party_name, accounts):
	"""accounts -- {company: (account, debtor_creditor_number)}"""
	if frappe.db.exists(party_type, party_name):
		return frappe.get_doc(party_type, party_name)

	prefix = party_type.lower()
	group_doctype = f"{party_type} Group"
	return frappe.get_doc(
		{
			"doctype": party_type,
			f"{prefix}_name": party_name,
			f"{prefix}_type": "Company",
			f"{prefix}_group": frappe.db.get_value(group_doctype, {"is_group": 0}, "name"),
			"accounts": [
				{"company": company, "account": account, "debtor_creditor_number": number}
				for company, (account, number) in accounts.items()
			],
		}
	).insert()


def make_address(link_doctype, link_name, address_line1, is_primary):
	existing = frappe.db.get_value("Address", {"address_title": f"{link_name} {address_line1}"}, "name")
	if existing:
		return frappe.get_doc("Address", existing)

	return frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": f"{link_name} {address_line1}",
			"address_type": "Billing",
			"address_line1": address_line1,
			"city": "Berlin",
			"pincode": "10115",
			"country": "Germany",
			"is_primary_address": is_primary,
			"links": [{"link_doctype": link_doctype, "link_name": link_name}],
		}
	).insert()
