export namespace main {
	
	export class UpdateInfo {
	    available: boolean;
	    current: string;
	    latest: string;
	    notes: string;
	    url: string;
	    checkedAt: string;
	
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
	    }
	}
	export class WindowInfo {
	    title: string;
	    left: number;
	    top: number;
	    width: number;
	    height: number;
	
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
	    }
	}

}

