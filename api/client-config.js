import { publicConfig } from './_lib/monitoring.js';
export default function handler(req,res){if(req.method!=='GET')return res.status(405).json({error:'Use GET'});res.setHeader('Cache-Control','no-store');return res.status(200).json(publicConfig());}
