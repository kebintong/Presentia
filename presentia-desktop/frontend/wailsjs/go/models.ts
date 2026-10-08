export namespace main {
	
	export class ReleaseNote {
	    version: string;
	    notes: string;
	    date: string;
	
	    static createFrom(source: any = {}) {
	        return new ReleaseNote(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.version = source["version"];
	        this.notes = source["notes"];
	        this.date = source["date"];
	    }
	}
	export class UpdateInfo {
	    available: boolean;
	    current: string;
	    latest: string;
	    notes: string;
	    url: string;
	    checkedAt: string;
	    releases?: ReleaseNote[];
	    installed?: ReleaseNote;
	    pageUrl?: string;
	
	    static createFrom(source: any = {}) {
	        return new UpdateInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.available = source["available"];
	        this.current = source["current"];
	        this.latest = source["latest"];
	        this.notes = source["notes"];
	        this.url = source["url"];
	        this.checkedAt = source["checkedAt"];
	        this.releases = this.convertValues(source["releases"], ReleaseNote);
	        this.installed = this.convertValues(source["installed"], ReleaseNote);
	        this.pageUrl = source["pageUrl"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class WindowInfo {
	    title: string;
	    left: number;
	    top: number;
	    width: number;
	    height: number;
	    hwnd: number;
	
	    static createFrom(source: any = {}) {
	        return new WindowInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.title = source["title"];
	        this.left = source["left"];
	        this.top = source["top"];
	        this.width = source["width"];
	        this.height = source["height"];
	        this.hwnd = source["hwnd"];
	    }
	}

}

