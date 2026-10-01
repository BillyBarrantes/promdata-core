/**
 * Generates Playwright auth state from raw Supabase cookie values.
 * Run: node e2e/generate-auth.js
 */
const fs = require('fs');
const path = require('path');

// The two cookie parts from document.cookie (URL-encoded, split at ~4KB boundary)
const raw0 = `%7B%22access_token%22%3A%22eyJhbGciOiJIUzI1NiIsImtpZCI6IjBiTUpicU12am1rNmRCWG4iLCJ0eXAiOiJKV1QifQ.eyJpc3MiOiJodHRwczovL2R4bGtlanNydnVrbnVhamtsdHdtLnN1cGFiYXNlLmNvL2F1dGgvdjEiLCJzdWIiOiIzMGFlYWRhYS00OTc3LTQxNzMtOGM2NS1kZTEyMTQwYmEzNTMiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjoxNzg5NzU1OTcwLCJpYXQiOjE3ODk3NTIzNzAsImVtYWlsIjoibGJhcnJhbnRlc2R1QGdtYWlsLmNvbSIsInBob25lIjoiIiwiYXBwX21ldGFkYXRhIjp7InByb3ZpZGVyIjoiZ29vZ2xlIiwicHJvdmlkZXJzIjpbImdvb2dsZSJdfSwidXNlcl9tZXRhZGF0YSI6eyJhdmF0YXJfdXJsIjoiaHR0cHM6Ly9saDMuZ29vZ2xldXNlcmNvbnRlbnQuY29tL2EvQUNnOG9jTEw5ZHNZTFRZMVdLMTdsMV9ub3lqd3E2Q291V1dxWUxqeDZva2FNbkRUemhNd2dRPXM5Ni1jIiwiZW1haWwiOiJsYmFycmFudGVzZHVAZ21haWwuY29tIiwiZW1haWxfdmVyaWZpZWQiOnRydWUsImZ1bGxfbmFtZSI6Ikx1aWxseSBCYXJyYW50ZXMiLCJpc3MiOiJodHRwczovL2FjY291bnRzLmdvb2dsZS5jb20iLCJuYW1lIjoiTHVpbGx5IEJhcnJhbnRlcyIsInBob25lX3ZlcmlmaWVkIjpmYWxzZSwicGljdHVyZSI6Imh0dHBzOi8vbGgzLmdvb2dsZXVzZXJjb250ZW50LmNvbS9hL0FDZzhvY0xMOWRzWUxUWTFXSzE3bDFfbm95andxNkNvdVdXcVlMang2b2thTW5EVHpoTXdnUT1zOTYtYyIsInByb3ZpZGVyX2lkIjoiMTEzNDM1MjE4MzQ5NjUwMDM4MDUzIiwic3ViIjoiMTEzNDM1MjE4MzQ5NjUwMDM4MDUzIn0sInJvbGUiOiJhdXRoZW50aWNhdGVkIiwiYWFsIjoiYWFsMSIsImFtciI6W3sibWV0aG9kIjoib2F1dGgiLCJ0aW1lc3RhbXAiOjE3ODk3NTIzNzB9XSwic2Vzc2lvbl9pZCI6IjllODdlMzdiLWUxN2UtNDNmNi1hNWVmLTQ4MjVkZmIyMDU2NiIsImlzX2Fub255bW91cyI6ZmFsc2V9.3jHsVs6GxQkc0yUQiCIfPAKLyJ5ncAb3nl9HQHuDUhM%22%2C%22token_type%22%3A%22bearer%22%2C%22expires_in%22%3A3600%2C%22expires_at%22%3A1789755970%2C%22refresh_token%22%3A%224kh2p6cxvmg3%22%2C%22user%22%3A%7B%22id%22%3A%2230aeadaa-4977-4173-8c65-de12140ba353%22%2C%22aud%22%3A%22authenticated%22%2C%22role%22%3A%22authenticated%22%2C%22email%22%3A%22lbarrantesdu%40gmail.com%22%2C%22email_confirmed_at%22%3A%222025-12-07T22%3A16%3A10.464651Z%22%2C%22phone%22%3A%22%22%2C%22confirmed_at%22%3A%222025-12-07T22%3A16%3A10.464651Z%22%2C%22recovery_sent_at%22%3A%222026-09-05T14%3A35%3A06.920143Z%22%2C%22last_sign_in_at%22%3A%222026-09-18T17%3A26%3A10.503053495Z%22%2C%22app_metadata%22%3A%7B%22provider%22%3A%22google%22%2C%22providers%22%3A%5B%22google%22%5D%7D%2C%22user_metadata%22%3A%7B%22avatar_url%22%3A%22https%3A%2F%2Flh3.googleusercontent.com%2Fa%2FACg8ocLL9dsYLTY1WK17l1_noyjwq6CouWWqYLjx6okaMnDTzhMwgQ%3Ds96-c%22%2C%22email%22%3A%22lbarrantesdu%40gmail.com%22%2C%22email_verified%22%3Atrue%2C%22full_name%22%3A%22Luilly%20Barrantes%22%2C%22iss%22%3A%22https%3A%2F%2Faccounts.google.com%22%2C%22name%22%3A%22Luilly%20Barrantes%22%2C%22phone_verified%22%3Afalse%2C%22picture%22%3A%22https%3A%2F%2Flh3.googleusercontent.com%2Fa%2FACg8ocLL9dsYLTY1WK17l1_noyjwq6CouWWqYLjx6okaMnDTzhMwgQ%3Ds96-c%22%2C%22provider_id%22%3A%22113435218349650038053%22%2C%22sub%22%3A%22113435218349650038053%22%7D%2C%22identities%22%3A%5B%7B%22identity_id%22%3A%2249207cc8-bcbe-45f8-9788-ed9cf813c702%22%2C%22id%22%3A%22113435218349650038053%22%2C%22user_id%22%3A%2230aeadaa-4977-4173-8c65-de12140ba353%22%2C%22identity_data%22%3A%7B%22avatar_url%22%3A%22https%3A%2F%2Flh3.googleusercontent.com%2Fa%2FACg8ocLL9dsYLTY1WK17l1_noyjwq6CouWWqYLjx6okaMnDTzhMwgQ%3Ds96-c%22%2C%22email%22%3A%22lbarrantesdu%40gmail.com%22%2C%22email_verified%22%3Atrue%2C%22full_name%22%3A%22Luilly%20Barrantes%22%2C%22iss%22%3A%22ht`;

const raw1 = `tps%3A%2F%2Faccounts.google.com%22%2C%22name%22%3A%22Luilly%20Barrantes%22%2C%22phone_verified%22%3Afalse%2C%22picture%22%3A%22https%3A%2F%2Flh3.googleusercontent.com%2Fa%2FACg8ocLL9dsYLTY1WK17l1_noyjwq6CouWWqYLjx6okaMnDTzhMwgQ%3Ds96-c%22%2C%22provider_id%22%3A%22113435218349650038053%22%2C%22sub%22%3A%22113435218349650038053%22%7D%2C%22provider%22%3A%22google%22%2C%22last_sign_in_at%22%3A%222025-12-07T22%3A16%3A10.452205Z%22%2C%22created_at%22%3A%222025-12-07T22%3A16%3A10.452256Z%22%2C%22updated_at%22%3A%222026-09-18T17%3A26%3A06.764707Z%22%2C%22email%22%3A%22lbarrantesdu%40gmail.com%22%7D%5D%2C%22created_at%22%3A%222025-12-07T22%3A16%3A10.43676Z%22%2C%22updated_at%22%3A%222026-09-18T17%3A26%3A10.547072Z%22%2C%22is_anonymous%22%3Afalse%7D%2C%22provider_token%22%3A%22ya29.a0AdMD6Ej5nKJfDdDzY2TNr_Kmsun1kA9eywIHacBCu0HTyQGhQ6crKkF59TTPr4Ynt9HW7yEY1Hluj1vzHTJZ-hmny_eohhfFR3IAAy6PuVCEokmGxziPXyUKaqqknEAMiVhfKYZDhOObneN2JtUjApnp34v0diosoeEBoC-yQsdq_SdFbwabWGWvcxrq2UbhPU6jHw-fv8DbZgB7kTO0xdgeFKRJClHwujW5HnBZO5eVsNhh1u2CejxRz9pZuFD0TbQWSY2SiBOtyDY1deqe2HNAT01faCgYKAZoSARMSFQHGX2MixoyNsijqAlqbAeG1L-YX4Q0291%22`;

// The two cookie values are the JSON split across .0 and .1 — concatenate them
const decoded0 = decodeURIComponent(raw0);
const decoded1 = decodeURIComponent(raw1);
const fullJson = decoded0 + decoded1;

let session;
try {
  session = JSON.parse(fullJson);
} catch (e) {
  // Supabase splits the JSON across two cookies — the .0 part has the start,
  // the .1 part has the tail. We need to reconstruct the full JSON by
  // finding where .0 ends and .1 begins in the decoded string.
  // Actually the raw cookies ARE the full JSON — just split as cookie .0 and .1
  // The raw0 ends mid-JSON and raw1 continues it
  console.log('Direct parse failed, trying reconstructed...');
  
  // The raw cookies when concatenated decode to the full session JSON
  // But the split might be in the middle of a string value
  // Let's try: decode raw0 fully and see where it cuts
  console.log('raw0 decoded ends with:', decoded0.slice(-50));
  console.log('raw1 decoded starts with:', decoded1.slice(0, 50));
  
  process.exit(1);
}

const authState = {
  cookies: [{
    name: 'sb-dxlkejsrvuknuajkltwm-auth-token.0',
    value: raw0,
    domain: 'localhost',
    path: '/',
    expires: session.expires_at || -1,
    httpOnly: false,
    secure: false,
    sameSite: 'Lax'
  }, {
    name: 'sb-dxlkejsrvuknuajkltwm-auth-token.1',
    value: raw1,
    domain: 'localhost',
    path: '/',
    expires: session.expires_at || -1,
    httpOnly: false,
    secure: false,
    sameSite: 'Lax'
  }],
  origins: [{
    origin: 'http://localhost:3000',
    localStorage: []
  }]
};

const outPath = path.join(__dirname, '.auth', 'user.json');
fs.mkdirSync(path.join(__dirname, '.auth'), { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(authState, null, 2));
console.log('✅ Auth saved to:', outPath);
console.log('📧 User:', session.user.email);
console.log('⏰ Expires:', new Date(session.expires_at * 1000).toISOString());
