-- 按用户提供的 wolai 第3章建表，仅对新的课程库使用。
-- 执行前先核对现存库结构；不删除、不覆盖已有表。
CREATE DATABASE IF NOT EXISTS wechat
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS wechat.chathistory (
  chathistoryid INT AUTO_INCREMENT PRIMARY KEY,
  user_input TEXT,
  response TEXT,
  create_time DATETIME
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS wechat.new_chathistory (
  id INT DEFAULT NULL,
  time DATETIME DEFAULT NULL,
  records TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
