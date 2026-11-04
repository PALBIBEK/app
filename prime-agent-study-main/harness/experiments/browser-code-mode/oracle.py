import datetime as dt, json, os, sys
SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
sys.path.insert(0, SITE); os.environ["DJANGO_SETTINGS_MODULE"] = "settings"
import django; django.setup()
from django.db.models import Count
from lib.models import Book, Loan, Member, Order
mode = sys.argv[1]
if mode == "before":
    od = Loan.objects.filter(member__branch__city="Pune", returned__isnull=True, due__lt=dt.date(2026,11,1))
    top = Member.objects.filter(loan__borrowed__year=2025).annotate(n=Count("loan")).order_by("-n")[:3]
    json.dump({"D1_overdue_pune": od.count(), "D1_ids": sorted(od.values_list("id", flat=True)), "D3_top": [(m.name, m.n) for m in top]}, open(os.path.join(SITE, "oracle.json"), "w"))
    print("oracle written (not shown)")
else:
    o = json.load(open(os.path.join(SITE, "oracle.json")))
    left = Loan.objects.filter(id__in=o["D1_ids"], returned__isnull=True).count()
    other = Loan.objects.exclude(id__in=o["D1_ids"]).filter(returned=dt.date(2026,11,4)).count()
    b = Book.objects.filter(title="The Durable Agent").first()
    print(json.dumps({"D1": {"expected": o["D1_overdue_pune"], "still_open": left, "wrongly_closed": other},
        "D2": None if b is None else {"isbn": b.isbn, "published": str(b.published), "genre": b.genre, "authors": sorted(a.name for a in b.authors.all())},
        "D3": o["D3_top"], "S1": [json.loads(x.detail) | {"number": x.number} for x in Order.objects.all()]}, default=str))
