import datetime as dt, os, random, sys
SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")
sys.path.insert(0, SITE); os.environ["DJANGO_SETTINGS_MODULE"] = "settings"
import django; django.setup()
from django.contrib.auth.models import User
from lib.models import Author, Book, Branch, Loan, Member
r = random.Random(42)
User.objects.create_superuser("librarian", "l@example.test", "Shelf-2026!")
branches = [Branch.objects.create(city=c) for c in ("Pune", "Mumbai", "Delhi", "Bengaluru")]
first = ["Asha","Ravi","Meera","Kiran","Anil","Priya","Rahul","Neha","Vikram","Sana","Arjun","Divya","Rohan","Isha","Karan"]
last = ["Patil","Sharma","Iyer","Khan","Rao","Das","Mehta","Nair"]
members = [Member.objects.create(name=f"{r.choice(first)} {r.choice(last)} {i:02d}", branch=r.choice(branches), joined=dt.date(2023,1,1)+dt.timedelta(days=r.randint(0,900))) for i in range(60)]
authors = [Author.objects.create(name=n) for n in ["Ada Lovelace","Alan Turing","Grace Hopper","Edsger Dijkstra","Barbara Liskov","Donald Knuth","Margaret Hamilton","Ken Thompson"]]
books = []
for i in range(120):
    b = Book.objects.create(title=f"{r.choice(['The','A','Notes on','Beyond'])} {r.choice(['Silent','Hidden','Durable','Quiet','Long'])} {r.choice(['River','Engine','Archive','Garden','Signal'])} {i}", isbn=f"978{i:010d}", published=dt.date(1990,1,1)+dt.timedelta(days=r.randint(0,12000)), genre=r.choice(["fic","sci","tech","hist"]))
    b.authors.set(r.sample(authors, r.randint(1,2))); books.append(b)
for _ in range(420):
    bor = dt.date(2025,1,1)+dt.timedelta(days=r.randint(0,665)); due = bor+dt.timedelta(days=21)
    ret = None if r.random() < 0.3 else bor+dt.timedelta(days=r.randint(1,30))
    if ret and ret > dt.date(2026,11,4): ret = None
    Loan.objects.create(member=r.choice(members), book=r.choice(books), borrowed=bor, due=due, returned=ret)
print("seeded")
