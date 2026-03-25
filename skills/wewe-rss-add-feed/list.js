const { execSync } = require('child_process');

try {
  console.log('🔍 正在从底层数据库查询已订阅的公众号列表...');
  // 直接查库获取状态为开启的公众号
  const output = execSync(
    `docker exec -i wewe-rss-db-1 mysql --default-character-set=utf8mb4 -uroot -p123456 wewe-rss -e "SELECT mp_name, id FROM feeds WHERE status = 1;"`, 
    { encoding: 'utf-8' }
  );

  const lines = output.trim().split('\n');
  // lines[0] 是表头 "mp_name\tid"
  if (lines.length <= 1) {
    console.log('\n📭 目前蜘蛛网白名单中还没有订阅任何公众号。');
  } else {
    const records = lines.slice(1);
    console.log(`\n✅ 当前监控库中共有 ${records.length} 个公众号正在被静默抓取：`);
    console.log('----------------------------------------');
    records.forEach((line, index) => {
      const [name, id] = line.split('\t');
      console.log(`${index + 1}. ${name} (ID: ${id})`);
    });
    console.log('----------------------------------------');
  }
} catch (err) {
  console.error(`\n❌ 查询失败: 无法连接到 wewe-rss 数据库。详细错误: ${err.message}`);
}
