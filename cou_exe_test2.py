import tkinter as tk
from tkinter import messagebox, ttk
import asyncio
import re
import os
import requests
import pandas as pd
from io import BytesIO
from PIL import Image
from playwright.async_api import async_playwright
from datetime import datetime
import random
import threading
import sys

# 🛑 중단 신호용 변수
stop_event = threading.Event()
# ✨ 팝업창 제어 변수
work_popup = None

# --- [핵심] GUI에 콘솔 내용 표시하는 클래스 ---
class TextRedirector:
    def __init__(self, widget):
        self.widget = widget

    def write(self, str):
        self.widget.insert(tk.END, str)
        self.widget.see(tk.END) # 자동 스크롤
    
    def flush(self):
        pass

# --- 크롤링 함수들 ---
def clean_filename(filename):
    return re.sub(r'[\/:*?"<>|]', '', filename).strip()

def get_original_url(url):
    if not url: return url
    if url.startswith("//"): url = "https:" + url
    original = re.sub(r'/thumbnails/remote/[^/]+/', '/thumbnails/', url)
    original = original.replace("/thumbnails/", "/")
    return original

def merge_detail_images(image_urls, save_path):
    if not image_urls: return False
    downloaded_images = []
    total_height, max_width = 0, 0
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    for url in image_urls:
        try:
            if url.startswith("//"): url = "https:" + url
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                img = Image.open(BytesIO(resp.content))
                if img.width < 70 or img.height < 70: continue
                if img.mode in ("RGBA", "P"): img = img.convert("RGB")
                downloaded_images.append(img)
                total_height += img.height
                max_width = max(max_width, img.width)
        except: continue
        
    if not downloaded_images: return False
    
    canvas = Image.new('RGB', (max_width, total_height), (255, 255, 255))
    curr_y = 0
    for img in downloaded_images:
        canvas.paste(img, (0, curr_y))
        curr_y += img.height
    canvas.save(save_path, "JPEG", quality=85)
    return True

# --- 크롤링 핵심 로직 ---
async def crawl_logic(urls, goal_count, collect_options):
    global work_popup
    async with async_playwright() as p:
        try:
            start_time_str = datetime.now().strftime("%Y%m%d_%H%M")
            base_folder = f"images_{start_time_str}"
            os.makedirs(base_folder, exist_ok=True)
            
            print(f"🔗 크롬 연결 완료. 저장 폴더: {base_folder}")
            browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            context = browser.contexts[0]
            list_page = context.pages[0] 

            all_results = []
            
            for idx, url in enumerate(urls, 1):
                if not url.strip(): continue
                
                if stop_event.is_set():
                    print("🛑 수집 중단 신호를 받았습니다.")
                    break
                
                # ✨ 폴더명 형식 변경
                cat_folder = f"{base_folder}/{idx}번주소"
                print(f"🚀 [{idx}번주소] 수집 시작 (목표: {goal_count}개)")
                
                await list_page.goto(url)
                await asyncio.sleep(2)

                # 스크롤 동작 잘게 쪼개기
                for _ in range(15):
                    if stop_event.is_set(): break
                    await list_page.mouse.wheel(0, 500)
                    await asyncio.sleep(0.5)
                
                if stop_event.is_set(): break

                items = await list_page.query_selector_all("li.baby-product, li[data-id], [class*='ProductUnit_productItem']")
                
                success_count = 0
                processed_urls = set()
                processed_names = set()

                for item in items:
                    if stop_event.is_set(): break
                    if success_count >= goal_count: break 

                    try:
                        link_el = await item.query_selector("a")
                        if not link_el: continue
                        raw_url = await link_el.get_attribute("href")
                        detail_url = "https://www.coupang.com" + raw_url.split("?")[0]
                        if detail_url in processed_urls: continue

                        name_el = await item.query_selector("[class*='productName'], .name, .title")
                        if not name_el: continue
                        full_name = (await name_el.inner_text()).strip()
                        clean_name = clean_filename(full_name)
                        if clean_name in processed_names: continue

                        # 데이터 수집 (체크박스 반영)
                        brand_match = re.search(r'^\[(.*?)\]', full_name)
                        brand = brand_match.group(1) if brand_match else full_name.split()[0]
                        
                        product_data = {}
                        if collect_options["브랜드"]: product_data["브랜드명"] = brand
                        if collect_options["상품명"]: product_data["상품명"] = full_name
                        
                        # 원산지
                        origin = "국내산"
                        for o in ["호주", "미국", "캐나다", "스페인", "수입", "칠레", "멕시코", "필리핀", "노르웨이", "태국", "국내산", "페루", "뉴질랜드", "캘리포니아", "이스라엘", "제주", "브라질"]:
                            if o in full_name:
                                origin = o
                                break
                        if collect_options["원산지"]: product_data["원산지"] = origin
                        
                        # 냉장/냉동
                        storage = "냉동" if "냉동" in full_name else "냉장"
                        if collect_options["냉장/냉동"]: product_data["냉장/냉동"] = storage
                        
                        # ✨ ✨ ✨ [수정] g, kg, ml, L 모두 인식하게 수정 ✨ ✨ ✨
                        
                        # 1. 숫자와 단위 찾기 (g, kg, ml, L)
                        weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(g|kg|ml|L|KG|Kg|kG|ML|Ml)', full_name)
                        
                        # 2. 개수 찾기 (예: '4개', '2 개', '10입')
                        count_match = re.search(r'(\d+)\s*(개|팩)', full_name)
                        
                        final_weight = "직접확인"
                        
                        if weight_match and count_match:
                            try:
                                value = float(weight_match.group(1))
                                unit = weight_match.group(2).lower()
                                count_val = int(count_match.group(1))
                                
                                # 총량 계산
                                total_value = value * count_val
                                
                                # 최종 표기 및 단위 변환
                                if unit == 'kg' or unit == 'l':
                                    # kg나 l는 그대로 가거나 더 작으면 바꾸거나? 
                                    # 일단 계산된 대로 표기
                                    final_weight = f"{total_value}{unit}"
                                elif unit == 'g':
                                    if total_value >= 1000:
                                        final_weight = f"{total_value/1000}kg"
                                    else:
                                        final_weight = f"{total_value}g"
                                elif unit == 'ml':
                                    if total_value >= 1000:
                                        final_weight = f"{total_value/1000}L"
                                    else:
                                        final_weight = f"{total_value}ml"
                                
                            except:
                                final_weight = "계산실패"
                        elif weight_match:
                            # 개수가 없으면 그냥 중량만
                            final_weight = weight_match.group(0)
                            
                        if collect_options["중량/수량"]: product_data["중량/수량"] = final_weight
                        
                        # 가격
                        price = 0
                        if collect_options["가격"]:
                            price_elements = await item.query_selector_all("div.custom-oos, [class*='Price_priceValue']")
                            for p_el in price_elements:
                                if await p_el.query_selector("del"): continue
                                p_text = await p_el.inner_text()
                                match = re.search(r'([\d,]+)\s*원', p_text)
                                if match:
                                    price = int(re.sub(r'[^0-9]', '', match.group(1)))
                                    break
                            product_data["가격"] = price

                        if collect_options["상품URL"]: product_data["상품URL"] = detail_url

                        # --- 폴더 생성 및 이미지 저장 ---
                        product_folder = f"{cat_folder}/{clean_name}"
                        os.makedirs(product_folder, exist_ok=True)

                        # 상세페이지
                        detail_page = await context.new_page()
                        await detail_page.goto(detail_url)
                        await asyncio.sleep(random.uniform(2.0, 3.5))

                        # 대표 이미지
                        main_img_el = await detail_page.query_selector("img[class*='twc-'], .prod-image-detail")
                        if main_img_el:    
                            m_src = await main_img_el.get_attribute("src")
                            r = requests.get(get_original_url(m_src), headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                            if r.status_code == 200:
                                with open(f"{product_folder}/{clean_name}_대표이미지.jpg", "wb") as f: f.write(r.content)

                        # 상세 이미지
                        expand_btn = await detail_page.query_selector("button.product-detail-see-more")
                        if expand_btn: 
                            await expand_btn.click()
                            await asyncio.sleep(1.2)

                        img_els = await detail_page.query_selector_all(".product-detail-content img, .vendor-item img")
                        detail_urls = []
                        for img in img_els:
                            await img.scroll_into_view_if_needed()
                            await asyncio.sleep(0.1) 
                            box = await img.bounding_box()
                            if box and (box['width'] <= 100 and box['height'] <= 100): continue
                            
                            src = await img.get_attribute("data-src") or await img.get_attribute("src")
                            if src and "blank" not in src: detail_urls.append(get_original_url(src))

                        detail_urls = list(dict.fromkeys(detail_urls))
                        
                        img_saved = False
                        save_path = f"{product_folder}/{clean_name}_상세이미지.jpg"
                        if detail_urls:
                            img_saved = merge_detail_images(detail_urls, save_path)

                        if img_saved and os.path.exists(save_path):
                            all_results.append(product_data)
                            processed_urls.add(detail_url)
                            processed_names.add(clean_name)
                            success_count += 1
                            print(f"✅ [{idx}번주소-{success_count}/{goal_count}] 수집 완료: {clean_name}")
                        else:
                            print(f"⚠️ 실패/부족 (스킵): {clean_name}")
                            try:
                                if os.path.exists(f"{product_folder}/{clean_name}_대표이미지.jpg"):
                                    os.remove(f"{product_folder}/{clean_name}_대표이미지.jpg")
                                os.rmdir(product_folder)
                            except: pass
                        
                        await detail_page.close()

                    except Exception as e:
                        print(f"⚠️ 오류 발생 스킵: {e}")
                        continue
                
                if stop_event.is_set(): break

            if all_results:
                pd.DataFrame(all_results).to_excel(f"쿠팡_수집결과_{start_time_str}.xlsx", index=False)
                print(f"🏁 작업 완료! 총 {len(all_results)}개의 데이터 수집 완료.")
                messagebox.showinfo("완료", "수집이 완료되었습니다!")
            else:
                if stop_event.is_set():
                    messagebox.showinfo("중단", "수집이 중단되었습니다.")
                else:
                    messagebox.showwarning("결과", "수집된 데이터가 없습니다.")

        except Exception as e: 
            print(f"❌ 치명적 에러: {e}")
            messagebox.showerror("에러", str(e))
        finally:
            stop_event.clear()
            # ✨ 작업 종료 시 팝업 닫기
            if work_popup and work_popup.winfo_exists():
                work_popup.destroy()

# --- GUI 구성 ---
def start_thread():
    global work_popup
    stop_event.clear()
    
    # 버튼 누를 때 콘솔 출력을 텍스트 위젯으로 연결
    sys.stdout = TextRedirector(log_text)
    sys.stderr = TextRedirector(log_text)
    
    urls = [entry.get() for entry in url_entries]
    try:
        goal = int(goal_entry.get())
    except:
        messagebox.showwarning("경고", "목표 개수는 숫자로 입력해주세요.")
        return

    # ✨✨✨ [추가] 작업 중 팝업창 띄우기 (중단 버튼 포함) ✨✨✨
    work_popup = tk.Toplevel(root)
    work_popup.title("작업 중")
    work_popup.geometry("300x120")
    work_popup.protocol("WM_DELETE_WINDOW", lambda: None) # 👈 X 버튼 비활성화
    
    tk.Label(work_popup, text="작업 중입니다...\n가급적 조작하지 마시오.", font=('Arial', 10, 'bold'), pady=10).pack()
    
    # ✨✨✨ [추가] 팝업창에 중단 버튼 만들기 ✨✨✨
    stop_btn = tk.Button(work_popup, text="지금 중단하기", command=stop_crawling, bg="#f44336", fg="white", font=('Arial', 9, 'bold'))
    stop_btn.pack(pady=5)
    
    work_popup.grab_set() # 👈 메인 창 조작 막기

    options = {
        "브랜드": var_brand.get(),
        "상품명": var_name.get(),
        "원산지": var_origin.get(),
        "냉장/냉동": var_storage.get(),
        "중량/수량": var_weight.get(),
        "가격": var_price.get(),
        "상품URL": var_url.get()
    }

    t = threading.Thread(target=lambda: asyncio.run(crawl_logic(urls, goal, options)))
    t.start()

def stop_crawling():
    global work_popup
    stop_event.set()
    print("🛑 중단 버튼이 눌렸습니다. 대기 중...")
    
    # ✨✨✨ [추가] 작업 중 팝업창 닫기 ✨✨✨
    if work_popup and work_popup.winfo_exists():
        work_popup.destroy()

def add_url_entry():
    row = len(url_entries) + 1
    tk.Label(url_frame, text=f"{row}.").grid(row=row, column=0, padx=5, pady=0)
    entry = tk.Entry(url_frame, width=70)
    entry.grid(row=row, column=1, pady=0)
    url_entries.append(entry)

root = tk.Tk()
root.title("쿠팡 수집기 v1.6")
root.geometry("650x700") 

# 목표 개수
goal_frame = tk.Frame(root)
goal_frame.pack(pady=5)
tk.Label(goal_frame, text="💡 카테고리당 목표 개수: ").pack(side=tk.LEFT)
goal_entry = tk.Entry(goal_frame, width=10)
goal_entry.insert(0, "30")
goal_entry.pack(side=tk.LEFT, padx=5)

# 데이터 선택
check_frame = tk.LabelFrame(root, text="📊 수집할 데이터 선택", padx=10, pady=5)
check_frame.pack(pady=5, padx=10, fill="x")

var_brand = tk.BooleanVar(value=True)
var_name = tk.BooleanVar(value=True)
var_origin = tk.BooleanVar(value=True)
var_storage = tk.BooleanVar(value=True)
var_weight = tk.BooleanVar(value=True)
var_price = tk.BooleanVar(value=True)
var_url = tk.BooleanVar(value=True)

tk.Checkbutton(check_frame, text="브랜드", variable=var_brand).grid(row=0, column=0, sticky="w", pady=0)
tk.Checkbutton(check_frame, text="상품명", variable=var_name).grid(row=0, column=1, sticky="w", pady=0)
tk.Checkbutton(check_frame, text="원산지", variable=var_origin).grid(row=0, column=2, sticky="w", pady=0)
tk.Checkbutton(check_frame, text="냉장/냉동", variable=var_storage).grid(row=1, column=0, sticky="w", pady=0)
tk.Checkbutton(check_frame, text="중량/수량", variable=var_weight).grid(row=1, column=1, sticky="w", pady=0)
tk.Checkbutton(check_frame, text="가격", variable=var_price).grid(row=1, column=2, sticky="w", pady=0)
tk.Checkbutton(check_frame, text="상품URL", variable=var_url).grid(row=2, column=0, sticky="w", pady=0)

# URL 입력
url_frame = tk.LabelFrame(root, text="🔗 URL 목록", padx=10, pady=5)
url_frame.pack(pady=5, padx=10, fill="both", expand=True)

url_entries = []
add_url_entry()

# 버튼 구역
btn_frame = tk.Frame(root)
btn_frame.pack(pady=5)
tk.Button(btn_frame, text="➕ URL 추가", command=add_url_entry).pack(side=tk.LEFT, padx=10)
tk.Button(btn_frame, text="🚀 수집 시작", command=start_thread, bg="#4CAF50", fg="white", font=('Arial', 10, 'bold'), width=15).pack(side=tk.LEFT, padx=10)
tk.Button(btn_frame, text="🛑 수집 중단", command=stop_crawling, bg="#f44336", fg="white", font=('Arial', 10, 'bold'), width=15).pack(side=tk.LEFT, padx=10)

# 작업 현황창
log_frame = tk.LabelFrame(root, text="📝 작업 현황", padx=10, pady=5)
log_frame.pack(pady=5, padx=10, fill="both", expand=True)

log_text = tk.Text(log_frame, height=5, state='normal')
log_scroll = tk.Scrollbar(log_frame, command=log_text.yview)
log_text.configure(yscrollcommand=log_scroll.set)

log_scroll.pack(side=tk.RIGHT, fill="y")
log_text.pack(side=tk.LEFT, fill="both", expand=True)

root.mainloop()