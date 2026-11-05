"""A hard single-page shop: shadow DOM, infinite scroll, a div listbox, a coupon from an
inbox tab, payment in an iframe, a native confirm dialog. The server checks the order."""
import json, random, secrets
from django.http import HttpResponse, JsonResponse
from django.urls import path
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import csrf_exempt
from .models import Order

rng = random.Random(11)
ADJ = ["Swift", "Trail", "Cloud", "Urban", "Peak", "Storm", "River", "Night"]
NOUN = ["Runner", "Walker", "Racer", "Hiker", "Glider"]
PRODUCTS = [{"id": i, "name": f"{rng.choice(ADJ)} {rng.choice(NOUN)} {chr(65 + i % 26)}{i}", "price": rng.randint(40, 180)} for i in range(300)]
PRODUCTS[237] = {"id": 237, "name": "Trail Runner X", "price": 129}

PAGE = r"""<!doctype html><html><head><title>Stride Shop</title><style>body{font-family:sans-serif} .hidden{display:none}
#modal{position:fixed;inset:10%;background:#fff;border:2px solid #333;padding:1em} </style></head><body>
<header><h1>Stride Shop</h1><span id=cart>Cart: 0 items</span> <a href="/shop/inbox" target="_blank">Inbox</a></header>
<product-list></product-list>
<div id=modal class=hidden><h2 id=pname></h2><p id=pprice></p>
 <div class=size-picker role=combobox aria-expanded=false tabindex=0>Choose size ▾</div>
 <ul class="size-options hidden" role=listbox>%SIZES%</ul>
 <button id=add disabled>Add to cart</button> <button id=close>Close</button></div>
<section id=checkout class=hidden><h2>Checkout</h2><input id=coupon placeholder="Coupon code"><button id=apply>Apply</button>
 <p id=total></p><iframe id=pay src="/shop/pay" width=420 height=180></iframe><p id=result></p></section>
<script>
const cart=[];let current=null,size=null,coupon=null;
class ProductList extends HTMLElement{connectedCallback(){const r=this.attachShadow({mode:'open'});
 r.innerHTML='<style>.box{height:400px;overflow:auto;border:1px solid #999}.item{padding:6px;border-bottom:1px solid #eee}</style><div class=box><div class=items></div><div class=more>Loading more…</div></div>';
 this.box=r.querySelector('.box');this.items=r.querySelector('.items');this.offset=0;this.busy=false;
 this.box.addEventListener('scroll',()=>{if(this.box.scrollTop+this.box.clientHeight>this.box.scrollHeight-40)this.load()});this.load()}
 async load(){if(this.busy||this.offset>=300)return;this.busy=true;await new Promise(r=>setTimeout(r,400));
  const res=await fetch('/shop/api/products?offset='+this.offset);const data=await res.json();
  for(const p of data){const d=document.createElement('div');d.className='item';d.innerHTML='<span class=name>'+p.name+'</span> — £'+p.price+' <button class=view>View</button>';
   d.querySelector('.view').onclick=()=>openModal(p);this.items.appendChild(d)}
  this.offset+=data.length;this.busy=false;if(this.offset>=300)this.shadowRoot.querySelector('.more').textContent='End of list'}}
customElements.define('product-list',ProductList);
function openModal(p){current=p;size=null;document.getElementById('pname').textContent=p.name;document.getElementById('pprice').textContent='£'+p.price;
 document.getElementById('add').disabled=true;document.querySelector('.size-picker').textContent='Choose size ▾';document.getElementById('modal').classList.remove('hidden')}
document.querySelector('.size-picker').onclick=e=>{const l=document.querySelector('.size-options');l.classList.toggle('hidden');e.target.setAttribute('aria-expanded',!l.classList.contains('hidden'))};
document.querySelectorAll('.size-options li').forEach(li=>li.onclick=()=>{size=li.dataset.size;document.querySelector('.size-picker').textContent='Size '+size+' ▾';
 document.querySelector('.size-options').classList.add('hidden');setTimeout(()=>document.getElementById('add').disabled=false,700)});
document.getElementById('close').onclick=()=>document.getElementById('modal').classList.add('hidden');
document.getElementById('add').onclick=()=>{cart.push({id:current.id,size});document.getElementById('cart').textContent='Cart: '+cart.length+' items';
 document.getElementById('modal').classList.add('hidden');document.getElementById('checkout').classList.remove('hidden');total()};
function total(){fetch('/shop/api/quote',{method:'POST',body:JSON.stringify({cart,coupon})}).then(r=>r.json()).then(q=>{document.getElementById('total').textContent=q.error?q.error:'Total: £'+q.total})}
document.getElementById('apply').onclick=()=>{coupon=document.getElementById('coupon').value.trim();total()};
window.addEventListener('message',async e=>{if(e.data&&e.data.card){const t=document.getElementById('total').textContent;
 if(!confirm('Place order? '+t))return;const r=await fetch('/shop/api/order',{method:'POST',body:JSON.stringify({cart,coupon,card:e.data.card})});
 const j=await r.json();document.getElementById('result').textContent=j.error?('Error: '+j.error):('Order placed: '+j.number)}});
</script></body></html>"""
SIZES = "".join(f'<li role=option data-size="{s}">EU {s}</li>' for s in range(38, 47))
PAY = """<!doctype html><html><body><form id=p><input name=card placeholder="Card number" autocomplete=off><input name=exp placeholder=MM/YY><input name=cvc placeholder=CVC>
<button>Pay</button></form><script>document.getElementById('p').onsubmit=e=>{e.preventDefault();const d=new FormData(e.target);
const c=d.get('card').replace(/\\s/g,'');if(!/^4242424242424242$/.test(c)||!/^\\d\\d\\/\\d\\d$/.test(d.get('exp'))||!/^\\d{3}$/.test(d.get('cvc'))){document.body.insertAdjacentHTML('beforeend','<p class=err>Card declined</p>');return}
parent.postMessage({card:c},'*')}</script></body></html>"""

def _code(request):
    if "coupon" not in request.session:
        request.session["coupon"] = "STRIDE-" + secrets.token_hex(3).upper()
    return request.session["coupon"]

def page(request): _code(request); return HttpResponse(PAGE.replace("%SIZES%", SIZES))
@xframe_options_sameorigin
def pay(request): return HttpResponse(PAY)
def inbox(request):
    code = _code(request)
    mails = [("Weekly digest", "Nothing new this week."), ("Your coupon", f"Use code <b class=code>{code}</b> for 15% off your next order."), ("Old coupon", "Use code <b class=code>STRIDE-OLD999</b> (expired)")]
    return HttpResponse("<html><head><title>Inbox</title></head><body><h1>Inbox</h1>" + "".join(f"<details><summary>{s}</summary><p>{b}</p></details>" for s, b in mails) + "</body></html>")
def products(request):
    o = int(request.GET.get("offset", 0)); return JsonResponse(PRODUCTS[o:o + 20], safe=False)
def _quote(request, body):
    items = [PRODUCTS[i["id"]] for i in body.get("cart", [])]
    total = sum(p["price"] for p in items)
    if body.get("coupon"):
        if body["coupon"] != request.session.get("coupon"): return None, "Invalid coupon"
        total = round(total * 0.85, 2)
    return total, None
@csrf_exempt
def quote(request):
    total, err = _quote(request, json.loads(request.body)); return JsonResponse({"error": err} if err else {"total": total})
@csrf_exempt
def order(request):
    body = json.loads(request.body); total, err = _quote(request, body)
    if err: return JsonResponse({"error": err})
    number = "SO-" + secrets.token_hex(3).upper()
    Order.objects.create(number=number, detail=json.dumps({"cart": body["cart"], "coupon": body.get("coupon"), "total": total}))
    return JsonResponse({"number": number})
urls = [path("shop", page), path("shop/pay", pay), path("shop/inbox", inbox), path("shop/api/products", products), path("shop/api/quote", quote), path("shop/api/order", order)]
