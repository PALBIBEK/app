from django.db import models
class Branch(models.Model):
    city = models.CharField(max_length=40)
    def __str__(self): return self.city
class Member(models.Model):
    name = models.CharField(max_length=80)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    joined = models.DateField()
    def __str__(self): return self.name
class Author(models.Model):
    name = models.CharField(max_length=80)
    def __str__(self): return self.name
class Book(models.Model):
    GENRES = [("fic", "Fiction"), ("sci", "Science"), ("tech", "Tech"), ("hist", "History")]
    title = models.CharField(max_length=120)
    isbn = models.CharField(max_length=13, unique=True)
    published = models.DateField()
    genre = models.CharField(max_length=4, choices=GENRES)
    authors = models.ManyToManyField(Author)
    def __str__(self): return self.title
class Loan(models.Model):
    member = models.ForeignKey(Member, on_delete=models.CASCADE)
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    borrowed = models.DateField()
    due = models.DateField()
    returned = models.DateField(null=True, blank=True)
class Order(models.Model):
    number = models.CharField(max_length=20)
    detail = models.TextField()
