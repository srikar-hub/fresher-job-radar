import { env } from "cloudflare:test";
import { beforeAll, describe, it, expect, vi } from "vitest";
import worker, { type Env } from "../src/index";
import migrationSql from "../migrations/0001_initial.sql?raw";
import jobLookupMigration from "../migrations/0002_job_url_lookup.sql?raw";
import scannerIntegrationMigration from "../migrations/0003_scanner_integration.sql?raw";
import phase7Migration from "../migrations/0004_phase7_bot_management.sql?raw";
import phase8Migration from "../migrations/0005_optional_ai.sql?raw";
const testEnv = env as unknown as Env;
const request = (path: string, method = "GET", payload?: unknown) => new Request(`http://local/api/v1${path}`, { method, headers: { authorization: `Bearer ${testEnv.SCANNER_API_SECRET}`, ...(payload === undefined ? {} : { "content-type": "application/json" }) }, ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
let seedChannelId: number;

describe("scanner API auth and D1 operations", () => {
  beforeAll(async () => {
    testEnv.SCANNER_API_SECRET = "unit-test-only-secret";
    for (const statement of migrationSql.replace(/^PRAGMA foreign_keys = ON;\s*/i, "").split(";")) {
      if (statement.trim()) await testEnv.DB.prepare(statement).run();
    }
    for (const statement of jobLookupMigration.split(";")) if (statement.trim()) await testEnv.DB.prepare(statement).run();
    for (const statement of scannerIntegrationMigration.split(";")) if (statement.trim()) await testEnv.DB.prepare(statement).run();
    for (const statement of phase7Migration.split(";")) if (statement.trim()) await testEnv.DB.prepare(statement).run();
    for (const statement of phase8Migration.split(";")) if (statement.trim()) await testEnv.DB.prepare(statement).run();
    testEnv.BOT_TOKEN = "test-bot-token";
    testEnv.OWNER_TELEGRAM_ID = "12345";
    testEnv.TELEGRAM_WEBHOOK_SECRET = "test-webhook-secret";
    await testEnv.DB.prepare("INSERT INTO channels(original_reference) VALUES('@SeedJobs')").run();
    seedChannelId = (await testEnv.DB.prepare("SELECT id FROM channels WHERE original_reference='@SeedJobs'").first<{id:number}>())!.id;
  });
  it("rejects unauthorized scanner calls", async () => {
    const response = await worker.fetch(new Request("http://local/api/v1/channels"), testEnv);
    expect(response.status).toBe(401);
  });
  it("creates and deduplicates pending channels", async () => {
    const request = () => new Request("http://local/api/v1/channels", { method: "POST", headers: { authorization: `Bearer ${testEnv.SCANNER_API_SECRET}`, "content-type": "application/json" }, body: JSON.stringify({ original_reference: "@ExampleJobs" }) });
    const first = await worker.fetch(request(), testEnv);
    const second = await worker.fetch(request(), testEnv);
    expect(first.status).toBe(201); expect(second.status).toBe(200);
  });
  it("persists fetched messages before monotonic checkpoint advance", async () => {
    const channel = await testEnv.DB.prepare("SELECT id FROM channels WHERE original_reference=?").bind("@ExampleJobs").first<{id:number}>();
    const id = channel!.id;
    const message = () => worker.fetch(new Request("http://local/api/v1/messages", { method:"POST", headers:{ authorization:`Bearer ${testEnv.SCANNER_API_SECRET}`, "content-type":"application/json" }, body:JSON.stringify({channel_id:id,telegram_message_id:7,processing_status:"PROCESSED"}) }),testEnv);
    expect((await message()).status).toBe(200);
    const advance = (last_message_id:number,processed_message_ids=[7]) => worker.fetch(new Request(`http://local/api/v1/channels/${id}/checkpoint`, { method:"POST", headers:{ authorization:`Bearer ${testEnv.SCANNER_API_SECRET}`, "content-type":"application/json" }, body:JSON.stringify({last_message_id,processed_message_ids}) }),testEnv);
    expect((await advance(7)).status).toBe(200);
    expect((await advance(6,[6])).status).toBe(409);
  });
  it("rejects checkpoint movement past unrecorded fetched posts", async () => {
    const channel = await testEnv.DB.prepare("SELECT id FROM channels WHERE original_reference=?").bind("@ExampleJobs").first<{id:number}>();
    const response = await worker.fetch(new Request(`http://local/api/v1/channels/${channel!.id}/checkpoint`, { method:"POST", headers:{ authorization:`Bearer ${testEnv.SCANNER_API_SECRET}`, "content-type":"application/json" }, body:JSON.stringify({last_message_id:9,processed_message_ids:[9]}) }),testEnv);
    expect(response.status).toBe(409);
  });
  it("rejects invalid channel input and oversized request bodies", async () => {
    const headers={authorization:`Bearer ${testEnv.SCANNER_API_SECRET}`,"content-type":"application/json"};
    const invalid=await worker.fetch(new Request("http://local/api/v1/channels",{method:"POST",headers,body:"{}"}),testEnv);
    const oversized=await worker.fetch(new Request("http://local/api/v1/channels",{method:"POST",headers,body:JSON.stringify({x:"x".repeat(70_000)})}),testEnv);
    expect(invalid.status).toBe(400); expect(oversized.status).toBe(413);
  });
  it("deduplicates jobs carefully and retains multiple message sources", async () => {
    const secondChannel = await worker.fetch(request("/channels", "POST", { original_reference:"@SecondJobs" }),testEnv);
    const secondJson = await secondChannel.json() as {channel:{id:number}};
    for (const [channel_id,telegram_message_id] of [[seedChannelId,101],[secondJson.channel.id,202]]) {
      expect((await worker.fetch(request("/messages","POST",{channel_id,telegram_message_id,processing_status:"QUEUED"}),testEnv)).status).toBe(200);
    }
    const job = {id:"job-seed",company:"Acme",role:"Software Engineer",classification:"MATCHED",match_score:88,normalized_application_url:"https://jobs.example/req-1",channel_id:seedChannelId,telegram_message_id:101,original_message_link:"https://t.me/seed/101"};
    const first = await (await worker.fetch(request("/jobs","POST",job),testEnv)).json() as {id:string;duplicate:boolean};
    const duplicate = await (await worker.fetch(request("/jobs","POST",{...job,id:"job-copy",channel_id:secondJson.channel.id,telegram_message_id:202}),testEnv)).json() as {id:string;duplicate:boolean};
    expect(first).toEqual({id:"job-seed",duplicate:false});
    expect(duplicate).toEqual({id:"job-seed",duplicate:true});
    const sources = await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM job_sources WHERE job_id='job-seed'").first<{n:number}>();
    expect(sources?.n).toBe(2);
    await worker.fetch(request("/jobs","POST",{id:"job-rejected",company:"Other",role:"Engineer",classification:"REJECTED",match_score:0}),testEnv);
    expect(await testEnv.DB.prepare("SELECT classification FROM jobs WHERE id='job-rejected'").first<{classification:string}>()).toEqual({classification:"REJECTED"});
  });
  it("persists classifier evidence, resolves cross-run job lookups and restores expired claims", async () => {
    await worker.fetch(request("/messages", "POST", { channel_id:seedChannelId, telegram_message_id:909, processing_status:"PROCESSING", content_hash:"hash-909" }), testEnv);
    await worker.fetch(request("/jobs", "POST", { id:"job-evidence", company:"Evidence Co", role:"Software Engineer", classification:"REVIEW", match_score:60, explanation:"Experience unclear", reasons:["Review eligibility"], evidence:["Batch 2026"], channel_id:seedChannelId, telegram_message_id:909 }), testEnv);
    const lookup = await worker.fetch(new Request(`http://local/api/v1/jobs/lookup?company=Evidence%20Co&role=Software%20Engineer&content_hash=hash-909`, {headers:{authorization:`Bearer ${testEnv.SCANNER_API_SECRET}`}}), testEnv);
    expect(lookup.status).toBe(200);
    const job = await worker.fetch(request("/jobs/job-evidence"), testEnv);
    const body = await job.json() as {explanation:string;reasons_json:string;evidence_json:string;sources:Array<unknown>};
    expect(body.explanation).toBe("Experience unclear");
    expect(JSON.parse(body.reasons_json)).toEqual(["Review eligibility"]);
    expect(JSON.parse(body.evidence_json)).toEqual(["Batch 2026"]);
    expect(body.sources).toHaveLength(1);
    await worker.fetch(request("/notifications", "POST", {id:"notice-expired", job_id:"job-evidence", recipient_telegram_user_id:"999"}), testEnv);
    await testEnv.DB.prepare("UPDATE notifications SET delivery_status='SENDING',lease_until='2000-01-01 00:00:00' WHERE id='notice-expired'").run();
    const pending = await worker.fetch(request("/notifications/pending"), testEnv);
    expect((await pending.json() as Array<{id:string}>).some(row => row.id === "notice-expired")).toBe(true);
    await worker.fetch(request("/notifications/notice-expired/claim", "POST", {owner:"test-cleanup"}), testEnv);
    await worker.fetch(request("/notifications/notice-expired/sent", "POST", {}), testEnv);
  });
  it("does not merge the same URL when requisition IDs identify separate vacancies", async () => {
    const shared = { company:"Distinct Corp", role:"Software Engineer", classification:"MATCHED", match_score:80, normalized_application_url:"https://jobs.example/open-role" };
    const first = await worker.fetch(request("/jobs", "POST", {...shared, id:"vacancy-req-one", requisition_id:"REQ-1001"}), testEnv);
    const second = await worker.fetch(request("/jobs", "POST", {...shared, id:"vacancy-req-two", requisition_id:"REQ-2002"}), testEnv);
    expect((await first.json() as {id:string;duplicate:boolean})).toEqual({id:"vacancy-req-one",duplicate:false});
    expect((await second.json() as {id:string;duplicate:boolean})).toEqual({id:"vacancy-req-two",duplicate:false});
    const lookup = await worker.fetch(new Request("http://local/api/v1/jobs/lookup?company=Distinct%20Corp&role=Software%20Engineer&url=https%3A%2F%2Fjobs.example%2Fopen-role&requisition_id=REQ-2002", {headers:{authorization:`Bearer ${testEnv.SCANNER_API_SECRET}`}}), testEnv);
    expect((await lookup.json() as {id:string}).id).toBe("vacancy-req-two");
  });
  it("claims outbox notifications once and supports controlled retry", async () => {
    await worker.fetch(request("/notifications","POST",{id:"notice-1",job_id:"job-seed",recipient_telegram_user_id:"123"}),testEnv);
    await worker.fetch(request("/notifications","POST",{id:"notice-duplicate",job_id:"job-seed",recipient_telegram_user_id:"123"}),testEnv);
    const claim = () => worker.fetch(request("/notifications/notice-1/claim","POST",{owner:"test-runner"}),testEnv);
    const claims = await Promise.all([claim(),claim()]);
    const claimResults = await Promise.all(claims.map(async r => (await r.json() as {claimed:boolean}).claimed));
    expect(claimResults.filter(Boolean)).toHaveLength(1);
    await worker.fetch(request("/notifications/notice-1/failed","POST",{error:"temporary",retry_at:"2000-01-01T00:00:00Z"}),testEnv);
    const pending = await worker.fetch(request("/notifications/pending"),testEnv);
    expect((await pending.json() as Array<unknown>)).toHaveLength(1);
    expect((await (await claim()).json() as {claimed:boolean}).claimed).toBe(true);
    await worker.fetch(request("/notifications/notice-1/sent","POST",{}),testEnv);
  });
  it("marks permanent notification failures without scheduling a retry", async () => {
    await worker.fetch(request("/notifications","POST",{id:"notice-permanent",job_id:"job-evidence",recipient_telegram_user_id:"123"}),testEnv);
    await worker.fetch(request("/notifications/notice-permanent/claim","POST",{owner:"test-runner"}),testEnv);
    await worker.fetch(request("/notifications/notice-permanent/failed","POST",{error:"invalid bot token",permanent:true}),testEnv);
    expect(await testEnv.DB.prepare("SELECT delivery_status FROM notifications WHERE id='notice-permanent'").first<{delivery_status:string}>()).toEqual({delivery_status:"FAILED"});
  });
  it("returns a generic retryable error when D1 fails", async () => {
    const unavailable = { ...testEnv, DB: { prepare: () => { throw new Error("database details must not be exposed"); } } } as unknown as Env;
    const response = await worker.fetch(new Request("http://local/api/v1/channels",{headers:{authorization:`Bearer ${testEnv.SCANNER_API_SECRET}`}}),unavailable);
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("database details");
  });
  it("keeps AI disabled by default and makes no provider call", async () => {
    testEnv.AI_ENABLED="false";
    const run=vi.fn(); testEnv.AI={run} as unknown as Ai;
    const response=await worker.fetch(request("/ai/classify","POST",{post:{description:"Hiring Software Engineer",graduation_batch_requirements:[]}}),testEnv);
    expect(response.status).toBe(503); expect(run).not.toHaveBeenCalled();
  });
  it("validates and caches mocked AI output without storing source post text", async () => {
    testEnv.AI_ENABLED="true"; testEnv.AI_MAX_REQUESTS_PER_DAY="10";
    const seenMessages: unknown[]=[];
    const run=vi.fn(async(_model:string,options:{messages:unknown[]})=>{ seenMessages.push(...options.messages); return {response:{decision:"REVIEW",reason:"Experience is unclear",evidence:["Software Engineer"],confidence:0.71,missing_information:["Experience"]}}; });
    testEnv.AI={run} as unknown as Ai;
    const post={title:"Software Engineer",company:null,description:"Hiring Software Engineer. Batch 2026.",experience_requirement:null,graduation_batch_requirements:[2026],location:null,work_arrangement:null,application_url:null};
    const first=await worker.fetch(request("/ai/classify","POST",{post}),testEnv);
    const second=await worker.fetch(request("/ai/classify","POST",{post}),testEnv);
    expect(first.status).toBe(200); expect(second.status).toBe(200); expect(run).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(seenMessages).toLowerCase()).toContain("untrusted data");
    expect((await second.json() as {cached:boolean}).cached).toBe(true);
    const outcomes=await worker.fetch(request("/ai/assessments?limit=5"),testEnv);
    expect((await outcomes.json() as Array<{assessment:{decision:string;provider:string;model:string;assessed_at:string}}>).some(item=>item.assessment?.decision==="REVIEW" && item.assessment.provider==="cloudflare-workers-ai" && item.assessment.model && item.assessment.assessed_at)).toBe(true);
    const stored=await testEnv.DB.prepare("SELECT status,result_json FROM ai_assessments ORDER BY assessed_at DESC LIMIT 1").first<{status:string;result_json:string}>();
    expect(stored?.status).toBe("COMPLETED"); expect(stored?.result_json).not.toContain(post.description);
  });
  it("rejects ungrounded or invalid AI output and applies an hourly cooldown", async () => {
    testEnv.AI_ENABLED="true";
    const run=vi.fn(async()=>({response:{decision:"MATCHED",reason:"The job is suitable",evidence:["Freshers welcome"],confidence:0.99,missing_information:[]}}));
    testEnv.AI={run} as unknown as Ai;
    const post={title:null,company:null,description:"Hiring Software Engineer",experience_requirement:null,graduation_batch_requirements:[],location:null,work_arrangement:null,application_url:null};
    const first=await worker.fetch(request("/ai/classify","POST",{post}),testEnv);
    const second=await worker.fetch(request("/ai/classify","POST",{post}),testEnv);
    expect(first.status).toBe(503); expect(second.status).toBe(503); expect(run).toHaveBeenCalledTimes(1);
  });
  it("enforces a local daily AI request cap", async () => {
    testEnv.AI_ENABLED="true"; testEnv.AI_MAX_REQUESTS_PER_DAY="1";
    await testEnv.DB.prepare("DELETE FROM ai_daily_usage WHERE utc_date=date('now')").run();
    const run=vi.fn(async()=>({response:{decision:"REVIEW",reason:"Unclear",evidence:[],confidence:0.5,missing_information:["Experience"]}}));
    testEnv.AI={run} as unknown as Ai;
    const post=(description:string)=>({title:null,company:null,description,experience_requirement:null,graduation_batch_requirements:[],location:null,work_arrangement:null,application_url:null});
    const one=await worker.fetch(request("/ai/classify","POST",{post:post("Software Engineer hiring")}),testEnv);
    const two=await worker.fetch(request("/ai/classify","POST",{post:post("Software Engineer role")}),testEnv);
    expect(one.status).toBe(200); expect(two.status).toBe(429); expect(run).toHaveBeenCalledTimes(1);
    testEnv.AI_MAX_REQUESTS_PER_DAY="10";
  });
  const webhook = (payload: unknown, secret = "test-webhook-secret") => new Request("http://local/telegram/webhook", {method:"POST",headers:{"content-type":"application/json", ...(secret ? {"X-Telegram-Bot-Api-Secret-Token":secret} : {})},body:typeof payload === "string" ? payload : JSON.stringify(payload)});
  const ownerUpdate = (id:number,text:string,chatType="private",sender=12345,chat=12345) => ({update_id:id,message:{text,from:{id:sender},chat:{id:chat,type:chatType}}});
  it("validates webhook secret and malformed payloads", async () => {
    const missing=await worker.fetch(webhook(ownerUpdate(1001,"/help"),""),testEnv);
    const invalid=await worker.fetch(webhook(ownerUpdate(1002,"/help"),"wrong-secret"),testEnv);
    const malformed=await worker.fetch(webhook("{"),testEnv);
    expect([missing.status,invalid.status,malformed.status]).toEqual([401,401,400]);
  });
  it("rejects an unauthorized sender and group commands without private-data replies", async () => {
    const send=vi.fn(async () => new Response(JSON.stringify({ok:true}),{status:200,headers:{"content-type":"application/json"}})); vi.stubGlobal("fetch",send);
    const unauthorized=await worker.fetch(webhook(ownerUpdate(1010,"/addchannel @privatejobs", "private",777,777)),testEnv);
    const group=await worker.fetch(webhook(ownerUpdate(1011,"/channels", "group")),testEnv);
    expect(unauthorized.status).toBe(200); expect(group.status).toBe(200); expect(send).not.toHaveBeenCalled();
    expect(await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM channels WHERE username='privatejobs'").first<{n:number}>()).toEqual({n:0});
    vi.unstubAllGlobals();
  });
  it("creates pending channel requests and deduplicates repeated webhook updates", async () => {
    const send=vi.fn(async () => new Response(JSON.stringify({ok:true}),{status:200,headers:{"content-type":"application/json"}})); vi.stubGlobal("fetch",send);
    const req=()=>worker.fetch(webhook(ownerUpdate(1020,"/addchannel https://t.me/PhaseSevenJobs")),testEnv);
    expect((await req()).status).toBe(200); expect((await req()).status).toBe(200);
    expect(await testEnv.DB.prepare("SELECT status,access_status FROM channels WHERE username='phasesevenjobs'").first()).toEqual({status:"PENDING_VERIFICATION",access_status:"UNKNOWN"});
    expect(send).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });
  it("persists global pause and rejects activating an unverified channel", async () => {
    const send=vi.fn(async () => new Response(JSON.stringify({ok:true}),{status:200,headers:{"content-type":"application/json"}})); vi.stubGlobal("fetch",send);
    await worker.fetch(webhook(ownerUpdate(1030,"/pause")),testEnv);
    expect(await (await worker.fetch(request("/settings/monitoring"),testEnv)).json()).toEqual({paused:true});
    await worker.fetch(webhook(ownerUpdate(1031,"/resumechannel @phasesevenjobs")),testEnv);
    expect(await testEnv.DB.prepare("SELECT status FROM channels WHERE username='phasesevenjobs'").first()).toEqual({status:"PENDING_VERIFICATION"});
    await worker.fetch(webhook(ownerUpdate(1032,"/resume")),testEnv);
    expect(await (await worker.fetch(request("/settings/monitoring"),testEnv)).json()).toEqual({paused:false});
    await testEnv.DB.prepare("UPDATE settings SET monitoring_preferences_json=? WHERE key='global'").bind(JSON.stringify({paused:true})).run();
    const lease=await worker.fetch(request("/leases/acquire","POST",{name:"scanner-global",owner:"paused-check"}),testEnv);
    expect((await lease.json() as {acquired:boolean}).acquired).toBe(false);
    await testEnv.DB.prepare("UPDATE settings SET monitoring_preferences_json=? WHERE key='global'").bind(JSON.stringify({paused:false})).run();
    vi.unstubAllGlobals();
  });
  it("pauses, resumes, removes, and requests verification without resetting the checkpoint", async () => {
    const send=vi.fn(async () => new Response(JSON.stringify({ok:true}),{status:200,headers:{"content-type":"application/json"}})); vi.stubGlobal("fetch",send);
    await testEnv.DB.prepare("UPDATE channels SET status='ACTIVE',access_status='VERIFIED',telegram_channel_id=-100123456,username='seedjobs' WHERE id=?").bind(seedChannelId).run();
    await testEnv.DB.prepare("INSERT INTO channel_checkpoints(channel_id,last_message_id) VALUES(?,45) ON CONFLICT(channel_id) DO UPDATE SET last_message_id=45").bind(seedChannelId).run();
    await worker.fetch(webhook(ownerUpdate(1033,"/pausechannel @seedjobs")),testEnv);
    expect(await testEnv.DB.prepare("SELECT status FROM channels WHERE id=?").bind(seedChannelId).first()).toEqual({status:"PAUSED"});
    await worker.fetch(webhook(ownerUpdate(1034,"/resumechannel @seedjobs")),testEnv);
    expect(await testEnv.DB.prepare("SELECT status FROM channels WHERE id=?").bind(seedChannelId).first()).toEqual({status:"ACTIVE"});
    await worker.fetch(webhook(ownerUpdate(1035,"/removechannel @seedjobs")),testEnv);
    expect(await testEnv.DB.prepare("SELECT status FROM channels WHERE id=?").bind(seedChannelId).first()).toEqual({status:"REMOVED"});
    await worker.fetch(webhook(ownerUpdate(1036,"/addchannel @seedjobs")),testEnv);
    expect(await testEnv.DB.prepare("SELECT status FROM channels WHERE id=?").bind(seedChannelId).first()).toEqual({status:"PENDING_VERIFICATION"});
    expect(await testEnv.DB.prepare("SELECT last_message_id FROM channel_checkpoints WHERE channel_id=?").bind(seedChannelId).first()).toEqual({last_message_id:45});
    vi.unstubAllGlobals();
  });
  it("returns retryable errors without leaking sensitive configuration when bot delivery fails", async () => {
    const send=vi.fn(async () => new Response(JSON.stringify({ok:false}),{status:429,headers:{"content-type":"application/json"}})); vi.stubGlobal("fetch",send);
    const errorLog=vi.spyOn(console,"error").mockImplementation(()=>{});
    const response=await worker.fetch(webhook(ownerUpdate(1040,"/settings")),testEnv);
    const body=await response.text();
    expect(response.status).toBe(503); expect(body).not.toContain("test-bot-token"); expect(body).not.toContain("test-webhook-secret");
    expect(await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM telegram_updates WHERE update_id=1040").first()).toEqual({n:0});
    const logs=errorLog.mock.calls.flat().join(" ");
    expect(logs).not.toContain("test-bot-token"); expect(logs).not.toContain("test-webhook-secret"); expect(logs).not.toContain("12345");
    errorLog.mockRestore();
    vi.unstubAllGlobals();
  });
  it("requires owner configuration and returns retryable D1 failures without details", async () => {
    const noOwner={...testEnv,OWNER_TELEGRAM_ID:undefined} as unknown as Env;
    expect((await worker.fetch(webhook(ownerUpdate(1050,"/help")),noOwner)).status).toBe(503);
    const broken={...testEnv,DB:{prepare:()=>{throw new Error("private database diagnostic");}}} as unknown as Env;
    const response=await worker.fetch(webhook(ownerUpdate(1051,"/help")),broken);
    expect(response.status).toBe(503); expect(await response.text()).not.toContain("private database diagnostic");
  });
  it("acknowledges unsupported Telegram update shapes and rejects unsafe channel identifiers", async () => {
    const send=vi.fn(async () => new Response(JSON.stringify({ok:true}),{status:200,headers:{"content-type":"application/json"}})); vi.stubGlobal("fetch",send);
    expect((await worker.fetch(webhook({update_id:1060,callback_query:{id:"x"}}),testEnv)).status).toBe(200);
    expect((await worker.fetch(webhook(ownerUpdate(1061,"/addchannel https://evil.example/channel")),testEnv)).status).toBe(200);
    expect((await worker.fetch(webhook(ownerUpdate(1062,"/addchannel t.me/+privateinvite")),testEnv)).status).toBe(200);
    expect(await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM channels WHERE id>0 AND (username='channel' OR username='privateinvite')").first()).toEqual({n:0});
    vi.unstubAllGlobals();
  });
  it("handles concurrent duplicate update deliveries with one channel mutation", async () => {
    const send=vi.fn(async () => new Response(JSON.stringify({ok:true}),{status:200,headers:{"content-type":"application/json"}})); vi.stubGlobal("fetch",send);
    const req=()=>worker.fetch(webhook(ownerUpdate(1070,"/addchannel @concurrentjobs")),testEnv);
    const responses=await Promise.all([req(),req()]);
    expect(responses.map(r=>r.status)).toEqual([200,200]);
    expect(await testEnv.DB.prepare("SELECT COUNT(*) AS n FROM channels WHERE username='concurrentjobs'").first()).toEqual({n:1});
    expect(send).toHaveBeenCalledTimes(1);
    vi.unstubAllGlobals();
  });
  it("keeps read-only commands bounded and does not reveal secrets in settings", async () => {
    const bodies:string[]=[];
    const send=vi.fn(async (_input:RequestInfo|URL,init?:RequestInit) => { bodies.push(String(init?.body ?? "")); return new Response(JSON.stringify({ok:true}),{status:200,headers:{"content-type":"application/json"}}); }); vi.stubGlobal("fetch",send);
    for (const [id,command] of [[1080,"/start"],[1081,"/help"],[1082,"/latest"],[1083,"/today"],[1084,"/stats"],[1085,"/settings"]] as const) {
      expect((await worker.fetch(webhook(ownerUpdate(id,command)),testEnv)).status).toBe(200);
    }
    const all=bodies.join(" ");
    expect(all).toContain("Fresher Job Radar"); expect(all).toContain("India Standard Time");
    expect(all).not.toContain("test-bot-token"); expect(all).not.toContain("test-webhook-secret"); expect(all).not.toContain(testEnv.SCANNER_API_SECRET);
    vi.unstubAllGlobals();
  });
  it("filters /today by original post date in India time, not database discovery time", async () => {
    const sent:string[]=[];
    const send=vi.fn(async (_input:RequestInfo|URL,init?:RequestInit) => { sent.push((JSON.parse(String(init?.body)) as {text:string}).text); return new Response(JSON.stringify({ok:true}),{status:200,headers:{"content-type":"application/json"}}); }); vi.stubGlobal("fetch",send);
    await testEnv.DB.prepare("UPDATE messages SET posted_at=datetime('now','-2 days') WHERE channel_id=? AND telegram_message_id=101").bind(seedChannelId).run();
    await worker.fetch(webhook(ownerUpdate(1088,"/today")),testEnv);
    expect(sent[0]).toContain("No matched jobs were posted today");
    await testEnv.DB.prepare("UPDATE messages SET posted_at=datetime('now','-330 minutes') WHERE channel_id=? AND telegram_message_id=101").bind(seedChannelId).run();
    await worker.fetch(webhook(ownerUpdate(1089,"/today")),testEnv);
    expect(sent[1]).toContain("Acme");
    vi.unstubAllGlobals();
  });
  it("caps channel listings below Telegram's message limit", async () => {
    const send=vi.fn(async (_input:RequestInfo|URL,init?:RequestInit) => { const body=JSON.parse(String(init?.body)); return new Response(JSON.stringify({ok:true,body}),{status:200,headers:{"content-type":"application/json"}}); }); vi.stubGlobal("fetch",send);
    for (let i=0;i<110;i++) await testEnv.DB.prepare("INSERT INTO channels(original_reference,username,title) VALUES(?,?,?)").bind(`@bounded${i}`,`bounded${i}`,"A channel name long enough to exercise output bounds").run();
    await worker.fetch(webhook(ownerUpdate(1090,"/channels")),testEnv);
    const sent=JSON.parse(String(send.mock.calls[0][1]?.body)).text as string;
    expect(sent.length).toBeLessThanOrEqual(3800);
    vi.unstubAllGlobals();
  });
});
