import os
import json
import re
import requests
from datetime import datetime, timedelta

# ---------- 配置 ----------
APP_ID = os.environ.get('FEISHU_APP_ID')
APP_SECRET = os.environ.get('FEISHU_APP_SECRET')
CHAT_ID = os.environ.get('FEISHU_CHAT_ID')

STOCKS_FILE = 'stocks.json'           # 股票列表文件
PROCESSED_IDS_FILE = '.processed_msg_ids.txt'  # 记录已处理的消息ID

# ---------- 飞书 API 封装 ----------
def get_tenant_access_token():
    """获取飞书 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    resp = requests.post(url, json=payload).json()
    return resp.get("tenant_access_token")

def get_messages(chat_id, hours_back=24):
    """获取群聊最近 hours_back 小时内的消息（最多 50 条）"""
    token = get_tenant_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    start_time = int((datetime.now() - timedelta(hours=hours_back)).timestamp() * 1000)
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    params = {
        "container_id_type": "chat",
        "container_id": chat_id,
        "start_time": start_time,
        "page_size": 50
    }
    resp = requests.get(url, headers=headers, params=params).json()
    if resp.get("code") != 0:
        print(f"获取消息失败: {resp}")
        return []
    return resp.get("data", {}).get("items", [])

def send_reply(msg):
    """（可选）发送回复消息到群聊，告知操作结果"""
    token = get_tenant_access_token()
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "receive_id": CHAT_ID,
        "msg_type": "text",
        "content": json.dumps({"text": msg})
    }
    requests.post(url, headers=headers, params={"receive_id_type": "chat"}, json=payload)

# ---------- 命令解析 ----------
def parse_commands(text):
    """
    从消息文本中解析出操作指令列表。
    支持格式：
       添加股票 600036
       删除股票 600036
    返回: list of (action, stock_code) ，action 为 'add' 或 'remove'
    """
    try:
        msg_content = json.loads(text)
        text_content = msg_content.get("text", "")
    except:
        text_content = text

    commands = []
    # 匹配 “添加股票 600036”
    add_match = re.search(r'添加股票\s*(\d{6})', text_content)
    if add_match:
        commands.append(('add', add_match.group(1)))
    # 匹配 “删除股票 600036”
    remove_match = re.search(r'删除股票\s*(\d{6})', text_content)
    if remove_match:
        commands.append(('remove', remove_match.group(1)))
    return commands

# ---------- 股票列表操作 ----------
def update_stocks_json(commands):
    """
    根据指令列表更新 stocks.json。
    返回 (是否修改, 添加成功的列表, 删除成功的列表)
    """
    with open(STOCKS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    stocks = data.get("stocks", [])

    added = []
    removed = []
    modified = False

    for action, code in commands:
        if action == 'add':
            if code not in stocks:
                stocks.append(code)
                added.append(code)
                modified = True
            else:
                print(f"股票 {code} 已存在，忽略添加")
        elif action == 'remove':
            if code in stocks:
                stocks.remove(code)
                removed.append(code)
                modified = True
            else:
                print(f"股票 {code} 不存在，忽略删除")

    if modified:
        data["stocks"] = stocks
        with open(STOCKS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    return modified, added, removed

def commit_and_push():
    """提交并推送到 GitHub"""
    os.system('git config user.name "github-actions[bot]"')
    os.system('git config user.email "github-actions[bot]@users.noreply.github.com"')
    os.system('git add stocks.json')
    os.system('git add .processed_msg_ids.txt')
    os.system('git commit -m "Auto update stocks from Feishu" || echo "No changes to commit"')
    os.system('git push')

# ---------- 消息去重管理 ----------
def load_processed_ids():
    """读取已处理过的消息ID集合"""
    if not os.path.exists(PROCESSED_IDS_FILE):
        return set()
    with open(PROCESSED_IDS_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def save_processed_ids(ids):
    """保存已处理的消息ID集合"""
    with open(PROCESSED_IDS_FILE, 'w', encoding='utf-8') as f:
        for msg_id in ids:
            f.write(msg_id + '\n')

# ---------- 主流程 ----------
def main():
    print("开始从飞书同步股票指令...")

    # 1. 获取最近24小时的消息
    messages = get_messages(CHAT_ID, hours_back=24)
    if not messages:
        print("未获取到任何消息")
        return

    # 2. 加载已处理的消息ID
    processed_ids = load_processed_ids()
    new_ids = set()

    # 3. 收集所有未处理过的指令
    all_commands = []  # 每条指令 (action, code)
    for msg in messages:
        msg_id = msg.get("message_id")
        if not msg_id or msg_id in processed_ids:
            continue
        # 解析消息内容
        content = msg.get("body", {}).get("content", "{}")
        commands = parse_commands(content)
        if commands:
            all_commands.extend(commands)
            new_ids.add(msg_id)
            print(f"发现新指令: {commands} (msg_id={msg_id})")

    if not all_commands:
        print("没有新的有效指令")
        return

    # 4. 执行股票列表更新
    modified, added, removed = update_stocks_json(all_commands)

    if modified:
        # 5. 记录已处理的消息ID
        processed_ids.update(new_ids)
        save_processed_ids(processed_ids)

        # 6. 提交并推送
        commit_and_push()

        # 7. 发送回复（可选）
        reply_parts = []
        if added:
            reply_parts.append(f"添加: {', '.join(added)}")
        if removed:
            reply_parts.append(f"删除: {', '.join(removed)}")
        if reply_parts:
            send_reply(f"股票列表已更新：{'；'.join(reply_parts)}")
        print("✅ 更新完成并已推送")
    else:
        print("股票列表无变化")

if __name__ == "__main__":
    main()
