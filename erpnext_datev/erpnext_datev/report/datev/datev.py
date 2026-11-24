"""
Provide a report and downloadable CSV according to the German DATEV format.

- Query report showing only the columns that contain data, formatted nicely for
	dispay to the user.
- CSV download functionality `download_datev_csv` that provides a CSV file with
	all required columns. Used to import the data into the DATEV Software.
"""

import json

import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe import _

from erpnext_datev.utils.datev_constants import (
	AccountNames,
	DebtorsCreditors,
	Transactions,
)
from erpnext_datev.utils.datev_csv import get_datev_csv, zip_and_download

COLUMNS = [
	{
		"label": "Umsatz (ohne Soll/Haben-Kz)",
		"fieldname": "Umsatz (ohne Soll/Haben-Kz)",
		"fieldtype": "Currency",
		"width": 100,
	},
	{
		"label": "Soll/Haben-Kennzeichen",
		"fieldname": "Soll/Haben-Kennzeichen",
		"fieldtype": "Data",
		"width": 100,
	},
	{"label": "Konto", "fieldname": "Konto", "fieldtype": "Data", "width": 100},
	{
		"label": "Gegenkonto (ohne BU-Schlüssel)",
		"fieldname": "Gegenkonto (ohne BU-Schlüssel)",
		"fieldtype": "Data",
		"width": 100,
	},
	{
		"label": "BU-Schlüssel",
		"fieldname": "BU-Schlüssel",
		"fieldtype": "Data",
		"width": 100,
	},
	{
		"label": "Belegdatum",
		"fieldname": "Belegdatum",
		"fieldtype": "Date",
		"width": 100,
	},
	{
		"label": "Belegfeld 1",
		"fieldname": "Belegfeld 1",
		"fieldtype": "Data",
		"width": 150,
	},
	{
		"label": "Buchungstext",
		"fieldname": "Buchungstext",
		"fieldtype": "Text",
		"width": 300,
	},
	{
		"label": "Beleginfo - Art 1",
		"fieldname": "Beleginfo - Art 1",
		"fieldtype": "Link",
		"options": "DocType",
		"width": 100,
	},
	{
		"label": "Beleginfo - Inhalt 1",
		"fieldname": "Beleginfo - Inhalt 1",
		"fieldtype": "Dynamic Link",
		"options": "Beleginfo - Art 1",
		"width": 150,
	},
	{
		"label": "Beleginfo - Art 2",
		"fieldname": "Beleginfo - Art 2",
		"fieldtype": "Link",
		"options": "DocType",
		"width": 100,
	},
	{
		"label": "Beleginfo - Inhalt 2",
		"fieldname": "Beleginfo - Inhalt 2",
		"fieldtype": "Dynamic Link",
		"options": "Beleginfo - Art 2",
		"width": 150,
	},
	{
		"label": "Beleginfo - Art 3",
		"fieldname": "Beleginfo - Art 3",
		"fieldtype": "Link",
		"options": "DocType",
		"width": 100,
	},
	{
		"label": "Beleginfo - Inhalt 3",
		"fieldname": "Beleginfo - Inhalt 3",
		"fieldtype": "Dynamic Link",
		"options": "Beleginfo - Art 3",
		"width": 150,
	},
	{
		"label": "Beleginfo - Art 4",
		"fieldname": "Beleginfo - Art 4",
		"fieldtype": "Data",
		"width": 100,
	},
	{
		"label": "Beleginfo - Inhalt 4",
		"fieldname": "Beleginfo - Inhalt 4",
		"fieldtype": "Data",
		"width": 150,
	},
	{
		"label": "Beleginfo - Art 5",
		"fieldname": "Beleginfo - Art 5",
		"fieldtype": "Data",
		"width": 150,
	},
	{
		"label": "Beleginfo - Inhalt 5",
		"fieldname": "Beleginfo - Inhalt 5",
		"fieldtype": "Data",
		"width": 100,
	},
	{
		"label": "Beleginfo - Art 6",
		"fieldname": "Beleginfo - Art 6",
		"fieldtype": "Data",
		"width": 150,
	},
	{
		"label": "Beleginfo - Inhalt 6",
		"fieldname": "Beleginfo - Inhalt 6",
		"fieldtype": "Date",
		"width": 100,
	},
	{
		"label": "Fälligkeit",
		"fieldname": "Fälligkeit",
		"fieldtype": "Date",
		"width": 100,
	},
]


def execute(filters=None):
	"""Entry point for frappe."""
	data = []
	message = None
	if filters and validate(filters):
		temp, opening = frappe.get_value(
			"DATEV Settings",
			filters.get("company"),
			["temporary_against_account_number", "opening_against_account_number"],
		)
		filters.update({"against_account": temp, "opening_account": opening or temp})
		data = get_transactions(filters, as_dict=0)
		message = get_missing_number_message(filters)

	return COLUMNS, data, message


def get_missing_number_message(filters):
	"""Warn about parties that will be missing from the DATEV master data files."""
	parties = get_parties_without_number(filters)
	if not parties:
		return None

	shown = parties[:20]
	items = "".join(
		"<li>{}: {}</li>".format(_(p.party_type), frappe.utils.escape_html(p.party)) for p in shown
	)
	more = ""
	if len(parties) > len(shown):
		more = "<p>" + _("... and {0} more.").format(len(parties) - len(shown)) + "</p>"

	return (
		"<p>"
		+ _(
			"These Customers and Suppliers have bookings in this period but no Debtor/Creditor Number"
			" for this company. They will be missing from the DATEV master data files."
			" Add the number in the Accounts table of each party."
		)
		+ "</p><ul>"
		+ items
		+ "</ul>"
		+ more
	)


def validate(filters):
	"""Make sure all mandatory filters and settings are present."""
	company = filters.get("company")
	if not company:
		frappe.throw(_("<b>Company</b> is a mandatory filter."))

	from_date = filters.get("from_date")
	if not from_date:
		frappe.throw(_("<b>From Date</b> is a mandatory filter."))

	to_date = filters.get("to_date")
	if not to_date:
		frappe.throw(_("<b>To Date</b> is a mandatory filter."))

	validate_fiscal_year(from_date, to_date, company)

	if not frappe.db.exists("DATEV Settings", filters.get("company")):
		msg = _("Please create DATEV Settings for Company {}").format(filters.get("company"))
		frappe.log_error(message=msg, title=_("DATEV Settings missing"))
		return False

	return True


def validate_fiscal_year(from_date, to_date, company):
	from_fiscal_year = get_fiscal_year(date=from_date, company=company)
	to_fiscal_year = get_fiscal_year(date=to_date, company=company)
	if from_fiscal_year != to_fiscal_year:
		frappe.throw(_("Dates {} and {} are not in the same fiscal year.").format(from_date, to_date))


def get_transactions(filters, as_dict=1):
	def run(params_method, filters):
		extra_fields, extra_joins, extra_filters = params_method(filters)
		return run_query(filters, extra_fields, extra_joins, extra_filters, as_dict=as_dict)

	def sort_by(row):
		# "Belegdatum" is in the fifth column when list format is used
		return row["Belegdatum" if as_dict else 5]

	type_map = {
		# specific query methods for some voucher types
		"Payment Entry": get_payment_entry_params,
		"Sales Invoice": get_sales_invoice_params,
		"Purchase Invoice": get_purchase_invoice_params,
	}

	only_voucher_type = filters.get("voucher_type")
	transactions = []

	for voucher_type, get_voucher_params in type_map.items():
		if only_voucher_type and only_voucher_type != voucher_type:
			continue

		transactions.extend(run(params_method=get_voucher_params, filters=filters))

	if not only_voucher_type or only_voucher_type not in type_map:
		# generic query method for all other voucher types
		filters["exclude_voucher_types"] = type_map.keys()
		transactions.extend(run(params_method=get_generic_params, filters=filters))

	return sorted(transactions, key=sort_by)


def get_payment_entry_params(filters):
	extra_fields = """
		, 'Zahlungsreferenz' as 'Beleginfo - Art 5'
		, pe.reference_no as 'Beleginfo - Inhalt 5'
		, 'Buchungstag' as 'Beleginfo - Art 6'
		, pe.reference_date as 'Beleginfo - Inhalt 6'
		, '' as 'Fälligkeit'
	"""

	extra_joins = """
		LEFT JOIN `tabPayment Entry` pe
		ON gl.voucher_no = pe.name
	"""

	extra_filters = """
		AND gl.voucher_type = 'Payment Entry'
	"""

	return extra_fields, extra_joins, extra_filters


def get_sales_invoice_params(filters):
	extra_fields = f"""
		, '' as 'Beleginfo - Art 5'
		, '' as 'Beleginfo - Inhalt 5'
		, '' as 'Beleginfo - Art 6'
		, '' as 'Beleginfo - Inhalt 6'
		, {get_due_date_field(filters, "si.due_date")} as 'Fälligkeit'
	"""

	extra_joins = """
		LEFT JOIN `tabSales Invoice` si
		ON gl.voucher_no = si.name
	"""

	extra_filters = """
		AND gl.voucher_type = 'Sales Invoice'
	"""

	return extra_fields, extra_joins, extra_filters


def get_purchase_invoice_params(filters):
	# DATEV allows max. 20 characters in "Beleginfo - Art"
	extra_fields = f"""
		, 'Lieferant-ReNummer' as 'Beleginfo - Art 5'
		, pi.bill_no as 'Beleginfo - Inhalt 5'
		, 'Lieferant-ReDatum' as 'Beleginfo - Art 6'
		, pi.bill_date as 'Beleginfo - Inhalt 6'
		, {get_due_date_field(filters, "pi.due_date")} as 'Fälligkeit'
	"""

	extra_joins = """
		LEFT JOIN `tabPurchase Invoice` pi
		ON gl.voucher_no = pi.name
	"""

	extra_filters = """
		AND gl.voucher_type = 'Purchase Invoice'
	"""

	return extra_fields, extra_joins, extra_filters


def get_due_date_field(filters, column):
	"""
	Return the SQL expression for the column "Fälligkeit".

	This export books against collective accounts, not DATEV personal accounts
	(OPOS). DATEV warns about every row that has a due date but no OPOS account,
	so the due date can be left out via DATEV Settings.
	"""
	exclude = frappe.db.get_value("DATEV Settings", filters.get("company"), "exclude_due_date")
	return "''" if exclude else column


def get_generic_params(filters):
	# produce empty fields so all rows will have the same length
	extra_fields = """
		, '' as 'Beleginfo - Art 5'
		, '' as 'Beleginfo - Inhalt 5'
		, '' as 'Beleginfo - Art 6'
		, '' as 'Beleginfo - Inhalt 6'
		, '' as 'Fälligkeit'
	"""
	extra_joins = ""

	if filters.get("exclude_voucher_types"):
		# exclude voucher types that are queried by a dedicated method
		exclude = "({})".format(", ".join("'{}'".format(key) for key in filters.get("exclude_voucher_types")))
		extra_filters = "AND gl.voucher_type NOT IN {}".format(exclude)

	# if voucher type filter is set, allow only this type
	if filters.get("voucher_type"):
		extra_filters += " AND gl.voucher_type = %(voucher_type)s"

	return extra_fields, extra_joins, extra_filters


def run_query(filters, extra_fields, extra_joins, extra_filters, as_dict=1):
	"""
	Get a list of accounting entries.

	Select GL Entries joined with Account and Party Account in order to get the
	account numbers. Returns a list of accounting entries.

	Arguments:
	filters -- dict of filters to be passed to the sql query
	as_dict -- return as list of dicts [0,1]
	"""
	query = """
		SELECT

			/* either debit or credit amount; always positive */
			case ROUND(gl.debit, 2) when 0 then ROUND(gl.credit, 2) else ROUND(gl.debit, 2) end as 'Umsatz (ohne Soll/Haben-Kz)',

			/* 'H' when credit, 'S' when debit */
			case ROUND(gl.debit, 2) when 0 then 'H' else 'S' end as 'Soll/Haben-Kennzeichen',

			/* account number or, if empty, party account number */
			acc.account_number as 'Konto',

			/* against number or, if empty, party against number */
			CASE gl.is_opening when 'Yes' then %(opening_account)s else %(against_account)s end as 'Gegenkonto (ohne BU-Schlüssel)',

			/* disable automatic VAT deduction */
			'' as 'BU-Schlüssel',

			gl.posting_date as 'Belegdatum',
			LEFT(gl.voucher_no, 36) as 'Belegfeld 1',
			REPLACE(LEFT(gl.remarks, 60), '\n', ' ') as 'Buchungstext',
			/* DATEV allows max. 20 characters in "Beleginfo - Art" */
			LEFT(gl.voucher_type, 20) as 'Beleginfo - Art 1',
			gl.voucher_no as 'Beleginfo - Inhalt 1',
			LEFT(gl.against_voucher_type, 20) as 'Beleginfo - Art 2',
			gl.against_voucher as 'Beleginfo - Inhalt 2',
			LEFT(gl.party_type, 20) as 'Beleginfo - Art 3',
			gl.party as 'Beleginfo - Inhalt 3',
			case gl.party_type when 'Customer' then 'Debitorennummer' when 'Supplier' then 'Kreditorennummer' else NULL end as 'Beleginfo - Art 4',
			par.debtor_creditor_number as 'Beleginfo - Inhalt 4'

			{extra_fields}

		FROM `tabGL Entry` gl

			/* Kontonummer */
			LEFT JOIN `tabAccount` acc
			ON gl.account = acc.name

			LEFT JOIN `tabParty Account` par
			ON par.parent = gl.party
			AND par.parenttype = gl.party_type
			AND par.company = %(company)s

			{extra_joins}

		WHERE gl.company = %(company)s
		/* cancelled vouchers and their reverse rows are not part of the books */
		AND gl.is_cancelled = 0
		AND DATE(gl.posting_date) >= %(from_date)s
		AND DATE(gl.posting_date) <= %(to_date)s

		{extra_filters}

		ORDER BY 'Belegdatum', gl.voucher_no""".format(
		extra_fields=extra_fields, extra_joins=extra_joins, extra_filters=extra_filters
	)

	gl_entries = frappe.db.sql(query, filters, as_dict=as_dict)

	return gl_entries


def get_customers(filters):
	"""
	Get the Customers of the selected company.

	Arguments:
	filters -- dict of filters to be passed to the sql query
	"""
	return get_parties(filters, "Customer")


def get_suppliers(filters):
	"""
	Get the Suppliers of the selected company.

	Arguments:
	filters -- dict of filters to be passed to the sql query
	"""
	return get_parties(filters, "Supplier")


def get_parties(filters, party_type):
	"""
	Get the master data of Customers or Suppliers in DATEV format.

	Only parties with a Debtor/Creditor Number for the selected company are
	returned. Without that number the party does not belong to this company's
	DATEV client, and DATEV would reject the row anyway (the account number is
	mandatory).

	The address is the party's primary address. If there is none, another
	enabled address is used (billing addresses first). Parties without any
	address are exported without address fields.

	Arguments:
	filters -- dict of filters to be passed to the sql query
	party_type -- "Customer" or "Supplier"
	"""
	if party_type not in ("Customer", "Supplier"):
		frappe.throw(_("Invalid party type: {0}").format(party_type))

	prefix = party_type.lower()
	extra_fields = ""
	if party_type == "Supplier":
		extra_fields = ", case party.on_hold when 1 then party.release_date else null end as 'Zahlungssperre bis'"

	return frappe.db.sql(
		f"""
		SELECT

			par.debtor_creditor_number as 'Konto',
			CASE party.{prefix}_type
				WHEN 'Company' THEN party.{prefix}_name
				ELSE null
				END as 'Name (Adressatentyp Unternehmen)',
			CASE party.{prefix}_type
				WHEN 'Individual' THEN TRIM(SUBSTR(party.{prefix}_name, LOCATE(' ', party.{prefix}_name)))
				ELSE null
				END as 'Name (Adressatentyp natürl. Person)',
			CASE party.{prefix}_type
				WHEN 'Individual' THEN SUBSTRING_INDEX(SUBSTRING_INDEX(party.{prefix}_name, ' ', 1), ' ', -1)
				ELSE null
				END as 'Vorname (Adressatentyp natürl. Person)',
			CASE party.{prefix}_type
				WHEN 'Individual' THEN '1'
				WHEN 'Company' THEN '2'
				ELSE '0'
				END as 'Adressatentyp',
			adr.address_line1 as 'Straße',
			adr.pincode as 'Postleitzahl',
			adr.city as 'Ort',
			UPPER(country.code) as 'Land',
			adr.address_line2 as 'Adresszusatz',
			adr.email_id as 'E-Mail',
			adr.phone as 'Telefon',
			adr.fax as 'Fax',
			party.website as 'Internet',
			party.tax_id as 'Steuernummer'
			{extra_fields}

		FROM `tab{party_type}` party

			/* only parties that have a Debtor/Creditor Number for this company */
			INNER JOIN `tabParty Account` par
			ON par.parent = party.name
			AND par.parenttype = %(party_type)s
			AND par.company = %(company)s
			AND IFNULL(par.debtor_creditor_number, '') != ''

			/* exactly one address: primary first, then billing, then the oldest */
			LEFT JOIN `tabAddress` adr
			ON adr.name = (
				SELECT a.name
				FROM `tabAddress` a
				INNER JOIN `tabDynamic Link` dl
				ON dl.parent = a.name
				AND dl.parenttype = 'Address'
				AND dl.link_doctype = %(party_type)s
				AND dl.link_name = party.name
				WHERE a.disabled = 0
				ORDER BY a.is_primary_address DESC, (a.address_type = 'Billing') DESC, a.creation ASC
				LIMIT 1
			)

			LEFT JOIN `tabCountry` country
			ON country.name = adr.country

		ORDER BY par.debtor_creditor_number
		""",
		dict(filters, party_type=party_type),
		as_dict=1,
	)


def get_parties_without_number(filters):
	"""
	Return Customers and Suppliers that have ledger entries in the selected
	period but no Debtor/Creditor Number for the selected company.

	These parties are missing from the DATEV master data files.
	"""
	return frappe.db.sql(
		"""
		SELECT DISTINCT gl.party_type, gl.party
		FROM `tabGL Entry` gl
		LEFT JOIN `tabParty Account` par
		ON par.parent = gl.party
		AND par.parenttype = gl.party_type
		AND par.company = gl.company
		WHERE gl.company = %(company)s
		AND gl.is_cancelled = 0
		AND gl.party_type IN ('Customer', 'Supplier')
		AND IFNULL(gl.party, '') != ''
		AND DATE(gl.posting_date) >= %(from_date)s
		AND DATE(gl.posting_date) <= %(to_date)s
		AND IFNULL(par.debtor_creditor_number, '') = ''
		ORDER BY gl.party_type, gl.party
		""",
		filters,
		as_dict=1,
	)


def get_account_names(filters):
	return frappe.db.sql(
		"""
		SELECT

			account_number as 'Konto',
			LEFT(account_name, 40) as 'Kontenbeschriftung',
			'de-DE' as 'Sprach-ID'

		FROM `tabAccount`
		WHERE company = %(company)s
		AND is_group = 0
		AND account_number != ''
	""",
		filters,
		as_dict=1,
	)


@frappe.whitelist(methods=["GET"])
def download_datev_csv(filters: str | dict):
	"""
	Provide accounting entries for download in DATEV format.

	Validate the filters, get the data, produce the CSV file and provide it for
	download. Can be called like this:

	GET /api/method/erpnext_datev.erpnext_datev.report.datev.datev.download_datev_csv

	Arguments / Params:
	filters -- str or dict of filters to be passed to the sql query
	"""
	frappe.only_for(["Accounts User", "Accounts Manager"])

	if isinstance(filters, str):
		filters = json.loads(filters)

	validate(filters)

	company = filters.get("company")
	company_doc = frappe.get_doc("Company", company)
	company_doc.check_permission() # user should not be restricted from accessing the company

	fiscal_year = get_fiscal_year(date=filters.get("from_date"), company=company)
	coa = company_doc.chart_of_accounts
	datev_settings = frappe.get_doc("DATEV Settings", company)

	filters.update(
		{
			"fiscal_year_start": fiscal_year[1],
			"skr": "04" if "SKR04" in coa else ("03" if "SKR03" in coa else ""),
			"account_number_length": datev_settings.account_number_length,
			"against_account": datev_settings.temporary_against_account_number,
			"opening_account": datev_settings.opening_against_account_number
			or datev_settings.temporary_against_account_number,
		}
	)

	transactions = get_transactions(filters)
	account_names = get_account_names(filters)
	customers = get_customers(filters)
	suppliers = get_suppliers(filters)

	zip_name = "{} DATEV.zip".format(frappe.utils.datetime.date.today())
	zip_and_download(
		zip_name,
		[
			{
				"file_name": "EXTF_Buchungsstapel.csv",
				"csv_data": get_datev_csv(transactions, filters, csv_class=Transactions),
			},
			{
				"file_name": "EXTF_Kontenbeschriftungen.csv",
				"csv_data": get_datev_csv(account_names, filters, csv_class=AccountNames),
			},
			{
				"file_name": "EXTF_Kunden.csv",
				"csv_data": get_datev_csv(customers, filters, csv_class=DebtorsCreditors),
			},
			{
				"file_name": "EXTF_Lieferanten.csv",
				"csv_data": get_datev_csv(suppliers, filters, csv_class=DebtorsCreditors),
			},
		],
	)
