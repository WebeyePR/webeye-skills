const { execSync } = require('child_process');

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function getRandomDelay(min, max) {
  return Math.floor(Math.random() * (max - min + 1) + min);
}

async function processUrl(url, isBatch) {
  try {
    const html = execSync(`curl -s -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" "${url}"`, { encoding: 'utf-8', maxBuffer: 1024 * 1024 * 10 });
    
    // 1. 抽取核心参数
    const bizMatch = html.match(/var biz = "([^"]+)"/);
    if (!bizMatch) throw new Error("无法从链接中解析出微信 biz 参数，可能链接格式不正确或失效");
    const base64Biz = bizMatch[1];
    const bizIdRaw = Buffer.from(base64Biz, 'base64').toString('utf-8');
    const mpId = `MP_WXS_${bizIdRaw}`;
    
    // 2. 提取名称、头像、简介
    let mpName = "未知公众号";
    const nameMatch = html.match(/var nickname = htmlDecode\("([^"]+)"\)/) || html.match(/nickname = "([^"]+)"/) || html.match(/var nickname = "([^"]+)"/);
    if (nameMatch) mpName = nameMatch[1];
    
    let mpCover = "";
    const coverMatch = html.match(/var round_head_img = "([^"]+)"/);
    if (coverMatch) mpCover = coverMatch[1];
    
    let mpIntro = "";
    const descMatch = html.match(/var msg_desc = htmlDecode\("([^"]+)"\)/);
    if (descMatch) mpIntro = descMatch[1];

    console.log(`[解析成功] 公众号: ${mpName} | ID: ${mpId}`);

    // 3. 强制用 utf8mb4 注入数据库防乱码
    console.log(`[数据入库] 正在写入 wewe-rss-db-1...`);
    const sql = `SET NAMES utf8mb4; INSERT IGNORE INTO feeds (id, mp_name, mp_cover, mp_intro, status, sync_time, update_time, has_history) VALUES ('${mpId}', '${mpName}', '${mpCover}', '${mpIntro}', 1, 0, 0, 1);`;
    execSync(`echo "${sql}" | docker exec -i wewe-rss-db-1 mysql --default-character-set=utf8mb4 -uroot -p123456 wewe-rss`);
    
    // 4. 判断是否触发热更新
    if (!isBatch) {
      console.log(`[触发抓取] 唤醒 wewe-rss-app-1 后台获取最新推文...`);
      const payload = JSON.stringify({ mpId });
      execSync(`env -u http_proxy -u https_proxy curl -s -X POST http://localhost:4000/trpc/feed.refreshArticles -H "Authorization: 123567" -H "Content-Type: application/json" -d '${payload}'`);
    } else {
      console.log(`[静默入库] 批量模式，跳过热启动，交由原生后台慢列队处理...`);
    }

    console.log(`🎉 [完成] “${mpName}” 处理完毕！\n`);
  } catch (err) {
    console.error(`❌ [执行失败] URL: ${url} -> ${err.message}\n`);
  }
}

async function main() {
  const urls = process.argv.slice(2);
  if (urls.length === 0) {
    console.error("请提供至少一个微信公众号文章链接！");
    process.exit(1);
  }

  const isBatch = urls.length >= 3;
  console.log(`🚀 开始执行任务，总计 ${urls.length} 个链接，批处理模式: ${isBatch}\n`);

  for (let i = 0; i < urls.length; i++) {
    const url = urls[i];
    console.log(`>>> 正在处理第 ${i + 1}/${urls.length} 个: ${url}`);
    await processUrl(url, isBatch);

    if (isBatch && i < urls.length - 1) {
      const delayMs = getRandomDelay(60000, 180000); // 1~3分钟随机
      console.log(`⏳ [防风控] 随机休眠 ${Math.round(delayMs / 1000)} 秒后继续...\n`);
      await sleep(delayMs);
    }
  }
  console.log("✅ 所有链接处理完毕！");
}

main();
