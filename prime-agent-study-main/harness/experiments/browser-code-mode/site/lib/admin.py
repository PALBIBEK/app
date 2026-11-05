import datetime
from django.contrib import admin
from django.template.response import TemplateResponse
from .models import Author, Book, Branch, Loan, Member, Order

@admin.action(description="Mark selected loans as returned")
def mark_returned(modeladmin, request, queryset):
    if request.POST.get("confirm") == "yes":
        n = queryset.update(returned=datetime.date(2026, 11, 4))
        modeladmin.message_user(request, f"{n} loans marked returned.")
        return None
    return TemplateResponse(request, "admin/lib/confirm_return.html", {**modeladmin.admin_site.each_context(request), "loans": queryset, "action": "mark_returned"})

class LoanInline(admin.TabularInline):
    model = Loan; extra = 0; fields = ("book", "borrowed", "due", "returned"); raw_id_fields = ("book",)

@admin.register(Member)
class MemberAdmin(admin.ModelAdmin):
    list_display = ("name", "branch", "joined"); list_filter = ("branch",); search_fields = ("name",); list_per_page = 15
    inlines = [LoanInline]

@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("id", "member", "book", "borrowed", "due", "returned")
    list_filter = ("member__branch", ("returned", admin.EmptyFieldListFilter), "due")
    search_fields = ("member__name", "book__title"); date_hierarchy = "borrowed"; list_per_page = 20
    actions = [mark_returned]; raw_id_fields = ("member", "book")

@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("title", "isbn", "published", "genre"); list_filter = ("genre",); search_fields = ("title", "isbn")
    filter_horizontal = ("authors",); list_per_page = 20

admin.site.register(Author, search_fields=("name",))
admin.site.register(Branch)
admin.site.register(Order)
