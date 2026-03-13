#!/usr/bin/env python3
"""
创建管理员用户脚本
- 自动连接数据库，生成 bcrypt 密码哈希
- 插入用户到 pd_users，并创建对应权限记录（所有权限为 1）
- 若账号已存在则跳过
"""

import os
import sys
import argparse
import pymysql
import bcrypt
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

def get_db_config():
    """从环境变量获取数据库连接配置"""
    required = ['MYSQL_HOST', 'MYSQL_PORT', 'MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_DATABASE']
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        print(f"错误: 缺少环境变量: {', '.join(missing)}")
        sys.exit(1)
    return {
        'host': os.getenv('MYSQL_HOST'),
        'port': int(os.getenv('MYSQL_PORT')),
        'user': os.getenv('MYSQL_USER'),
        'password': os.getenv('MYSQL_PASSWORD'),
        'database': os.getenv('MYSQL_DATABASE'),
        'charset': os.getenv('MYSQL_CHARSET', 'utf8mb4'),
        'autocommit': False,
    }

def hash_password(password: str) -> str:
    """生成 bcrypt 哈希（与 user_services.py 一致）"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')

def get_all_permission_fields(cursor):
    """从 pd_permission_definitions 获取所有权限字段名"""
    cursor.execute("SELECT field_name FROM pd_permission_definitions")
    rows = cursor.fetchall()
    return [row['field_name'] for row in rows]

def create_admin_user(account, password, name, dry_run=False):
    """
    创建管理员用户
    :param dry_run: 如果为 True，只打印 SQL 不执行
    """
    config = get_db_config()
    conn = pymysql.connect(**config, cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cursor:
            # 1. 检查账号是否已存在
            cursor.execute("SELECT id FROM pd_users WHERE account = %s", (account,))
            if cursor.fetchone():
                print(f"账号 '{account}' 已存在，跳过创建。")
                return

            # 2. 生成密码哈希
            pwd_hash = hash_password(password)

            # 3. 插入用户
            insert_user_sql = """
                INSERT INTO pd_users (name, account, password_hash, role, status)
                VALUES (%s, %s, %s, '管理员', 0)
            """
            params = (name, account, pwd_hash)
            if dry_run:
                print(f"[模拟] 执行 SQL: {insert_user_sql % params}")
                user_id = 1  # 模拟 ID
            else:
                cursor.execute(insert_user_sql, params)
                user_id = cursor.lastrowid
                print(f"用户插入成功，ID: {user_id}")

            # 4. 获取所有权限字段
            fields = get_all_permission_fields(cursor)
            if not fields:
                print("警告: 未找到权限定义表或权限字段为空，将只创建用户。")
                if not dry_run:
                    conn.commit()
                return

            # 5. 构建权限插入 SQL（所有权限设为 1）
            cols = ['user_id', 'role'] + fields
            placeholders = ','.join(['%s'] * len(cols))
            values = [user_id, '管理员'] + [1] * len(fields)

            insert_perm_sql = f"""
                INSERT INTO pd_user_permissions ({','.join(cols)})
                VALUES ({placeholders})
            """
            if dry_run:
                print(f"[模拟] 执行 SQL: {insert_perm_sql % tuple(values)}")
            else:
                cursor.execute(insert_perm_sql, tuple(values))
                print(f"权限记录插入成功，共 {len(fields)} 个权限字段。")

            if not dry_run:
                conn.commit()
                print("管理员创建完成！")
    except Exception as e:
        print(f"错误: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='创建管理员用户')
    parser.add_argument('--account', default='admin', help='登录账号 (默认: admin)')
    parser.add_argument('--password', required=True, help='密码 (必填)')
    parser.add_argument('--name', default='管理员', help='用户姓名 (默认: 管理员)')
    parser.add_argument('--dry-run', action='store_true', help='只打印 SQL，不实际执行')
    args = parser.parse_args()

    create_admin_user(
        account=args.account,
        password=args.password,
        name=args.name,
        dry_run=args.dry_run
    )