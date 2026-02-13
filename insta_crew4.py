import time
import pandas as pd
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext

# --- Selenium 드라이버 설정 ---
def get_driver():
    chrome_options = Options()
    chrome_options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
    driver = webdriver.Chrome(options=chrome_options)
    return driver

# 전역 변수 설정
collected_data = [] 
seen_ids = set()
stop_event = threading.Event() # --- [추가] 중단 신호용 이벤트 객체
popup = None # --- [추가] 팝업창 전역 변수 ---


# --- 수치 처리 함수 ---
def parse_count(count_str):
    try:
        count_str = str(count_str).replace(',', '').strip()
        count_str = ''.join(c for c in count_str if c.isdigit() or c in ['k', 'm', '만'])
        
        if '만' in count_str:
            return int(float(count_str.replace('만', '')) * 10000)
        if 'k' in count_str.lower():
            return int(float(count_str.lower().replace('k', '')) * 1000)
        if 'm' in count_str.lower():
            return int(float(count_str.lower().replace('m', '')) * 1000000)
        return int(''.join(filter(str.isdigit, count_str)))
    except: return 0

# --- 핵심 크롤링 로직 ---
def collect_from_tag(driver, tag, target_per_tag, log_widget):
    wait = WebDriverWait(driver, 15)
    
    def log(msg):
        log_widget.insert(tk.END, msg + "\n")
        log_widget.see(tk.END)
        print(msg)
    
    log(f"\n📢 '{tag}' 태그로 이동합니다...")
    driver.get(f"https://www.instagram.com/explore/tags/{tag}/")
    time.sleep(5) 
    
    log(f"📦 현재 태그 피드 로딩 중...")
    for _ in range(7):
        if stop_event.is_set(): return # --- [추가] 중간 확인
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.5)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)

    try:
        first_post_xpath = "//div[contains(@class, '_aagw')] | //article//a[contains(@href, '/p/')] | //div[@role='main']//a"
        first_post = wait.until(EC.presence_of_element_located((By.XPATH, first_post_xpath)))
        driver.execute_script("arguments[0].click();", first_post)
        log(f"✅ '{tag}' 게시물 진입 성공!")
        time.sleep(2)
    except Exception as e:
        log(f"❌ '{tag}' 첫 게시물 클릭 실패.")
        return

    count_in_tag = 0
    duplicate_streak = 0

    while count_in_tag < target_per_tag:
        if stop_event.is_set(): break # --- [추가] 루프 중단 확인
        try:
            driver.switch_to.window(driver.window_handles[0])
            u_id, u_url = "", ""
            
            for _ in range(5):
                try:
                    modal = driver.find_element(By.XPATH, "//div[@role='dialog'] | //article[@role='presentation']")
                    links = modal.find_elements(By.XPATH, ".//a[@role='link']")
                    for link in links:
                        txt, href = link.text.strip(), link.get_attribute('href')
                        if txt and href and '/p/' not in href and 'explore' not in href:
                            u_id, u_url = txt, href
                            break
                    if u_id: break
                except: pass
                time.sleep(0.5)

            if u_id and u_id not in seen_ids:
                duplicate_streak = 0
                log(f"🎯 [{tag}] 신규: {u_id}")
                
                driver.execute_script(f"window.open('{u_url}', '_blank');")
                wait.until(lambda d: len(d.window_handles) > 1)
                driver.switch_to.window(driver.window_handles[-1])
                time.sleep(3)

                try:
                    # 데이터 수집 (팔로워는 필수, 팔로잉은 0일 수 있음)
                    follower_el = wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/followers/')]//span")))
                    raw_follower = follower_el.text or "0"
                    
                    # 팔로잉은 에러가 나거나 0일 수 있으니 따로 처리
                    try:
                        following_el = driver.find_element(By.XPATH, "//a[contains(@href, '/following/')]//span")
                        raw_following = following_el.text or "0"
                    except:
                        raw_following = "0" # 팔로잉 못 읽으면 0
                    
                    # DM URL 추출
                    dm_url = u_url.strip('/') + "/message/"
                    
                    collected_data.append({
                        "수집 시간": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        "검색어": tag,
                        "ID": u_id,
                        "Followers": parse_count(raw_follower),
                        "Following": parse_count(raw_following), 
                        "URL": u_url,
                        "DM_URL": dm_url
                    })
                    seen_ids.add(u_id)
                    count_in_tag += 1
                    log(f"   └ [수집] 팔로워:{raw_follower} / 팔로잉:{raw_following}")
                    
                except Exception as e:
                    log(f"   └ [수집 실패] 데이터 가져오기 오류: {e}")

                driver.close()
                driver.switch_to.window(driver.window_handles[0])
            
            elif u_id in seen_ids:
                duplicate_streak += 1
                log(f"⚠️ [{tag}] 중복 패스: {u_id}") # --- [수정] 로그 추가! ---
                if duplicate_streak >= 7: break
            
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ARROW_RIGHT)
            time.sleep(1.5)

        except Exception as e:
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ARROW_RIGHT)
                time.sleep(1.5)
            except: break

# --- Tkinter UI 설정 ---
def start_collect():
    tags_input = tag_entry.get()
    count = count_entry.get()
    
    if not tags_input or not count:
        messagebox.showwarning("입력 오류", "태그(콤마 구분)와 수량을 모두 입력하세요.")
        return
    
    try:
        target = int(count)
    except:
        messagebox.showwarning("입력 오류", "수량은 숫자로 입력하세요.")
        return

    tags_list = [t.strip() for t in tags_input.split(',')]
    
    stop_event.clear() # --- [추가] 작업 시작 전 신호 초기화
    
    # 메인 스레드에서 팝업창을 생성하도록 요청
    app.after(0, create_popup)
    # --------------------------------

    
    t = threading.Thread(target=run_crawler, args=(tags_list, target))
    t.start()
    
def create_popup():
    global popup
    # Toplevel로 생성하되 확실하게 app(메인창)을 부모로 설정!
    popup = tk.Toplevel(app) 
    popup.title("알림")
    popup.geometry("300x150")
    popup.attributes("-topmost", True)
    # 닫기 버튼 비활성화 (작업 중 종료 방지)
    popup.protocol("WM_DELETE_WINDOW", lambda: None) 
    tk.Label(popup, text="🚫 동작 중입니다.\n\n가급적 조작하지 마세요!\n잠시만 기다려주세요...", font=("Malgun Gothic", 12, "bold")).pack(expand=True)
    popup.update()

    
def stop_collect(): # --- [추가] 중단 버튼 함수
    stop_event.set()
    log_text.insert(tk.END, "\n🛑 중단 요청 중... (현재 작업 완료 후 멈춥니다)\n")
    log_text.see(tk.END)

def run_crawler(tags_list, target):
    global popup
    start_btn.config(state=tk.DISABLED)
    stop_btn.config(state=tk.NORMAL) # --- [추가] 중단 버튼 활성화
    log_text.insert(tk.END, f"🚀 총 {len(tags_list)}개의 태그 작업 시작...\n")
    
    try:
        driver = get_driver()
        
        for tag in tags_list:
            if stop_event.is_set(): break # --- [추가] 태그 순환 멈춤
            if not tag: continue 
            collect_from_tag(driver, tag, target, log_text)
            log_text.insert(tk.END, f"✅ '{tag}' 작업 완료.\n")
            log_text.see(tk.END)
        
        # 엑셀 저장
        if collected_data:
            df = pd.DataFrame(collected_data)
            fn = f"insta_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            df.to_excel(fn, index=False)
            log_text.insert(tk.END, f"\n✨ 파일 저장 완료: {fn}\n")
            messagebox.showinfo("완료", "수집 및 저장 완료!")
        else:
            log_text.insert(tk.END, "\n⚠️ 수집된 데이터가 없습니다.\n")
        
    except Exception as e:
        messagebox.showerror("오류", str(e))
    finally:
        # --- [수정] 팝업창 확실하게 닫기! ---
        if popup:
            try:
                # 팝업창 닫기 명령을 메인 스레드로 안전하게 전달
                app.after(0, popup.destroy)
                popup = None
            except:
                pass
        start_btn.config(state=tk.NORMAL)
        stop_btn.config(state=tk.DISABLED)
        
# --- GUI 구성 ---
app = tk.Tk()
app.title("인스타 태그 크롤러")
app.geometry("400x600") # --- [수정] 높이 조금 늘림

tk.Label(app, text="검색 태그 (다수 입력 가능 ','로 구분):").pack(pady=5)
tag_entry = tk.Entry(app, width=40)
tag_entry.pack()
tk.Label(app, text="예: 노래,bts,헬스").pack()

tk.Label(app, text="태그당 수량:").pack(pady=5)
count_entry = tk.Entry(app)
count_entry.pack()

# --- [수정] 버튼 프레임 만들어서 옆으로 배치 ---
btn_frame = tk.Frame(app)
btn_frame.pack(pady=10)

start_btn = tk.Button(btn_frame, text="크롤링 시작", command=start_collect, width=15)
start_btn.pack(side=tk.LEFT, padx=5)

stop_btn = tk.Button(btn_frame, text="중단", command=stop_collect, width=15, state=tk.DISABLED)
stop_btn.pack(side=tk.LEFT, padx=5)

log_text = scrolledtext.ScrolledText(app, height=18)
log_text.pack(padx=10, pady=10)

app.mainloop()


### 일단 3차 완료버전